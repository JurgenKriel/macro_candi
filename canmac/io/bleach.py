"""Photobleaching correction — compute + store + lazily apply per-(t,c) factors.

D-07/D-08, IO-03. Uncorrected fluorescence decay makes late-frame Candida dimmer,
which downstream intensity/"killing" steps misread as death — "bleaching mimics
killing" (RESEARCH Pitfall 3). This module removes that bias by:

1. computing a per-(timepoint, channel) multiplicative correction factor on the
   **foreground** signal — never the whole-volume mean (background/empty voxels
   dominate as signal decays, flattening the curve wrongly; RESEARCH Pattern 4);
2. storing those factors to ``results/{roi}/bleach.json`` atomically (temp ->
   ``os.replace`` + a ``.done`` sentinel written LAST, via :mod:`canmac.io.atomic`);
3. applying them **lazily on read** inside :func:`canmac.io.reader.get_view` — the
   ~32 GB ``raw.zarr`` volumes are NEVER rewritten.

Method (D-07 discretion): the default is a transparent ratio-to-reference on the
foreground mean (``f[t] = S[ref] / S[t]``, the Fiji "Simple Ratio" analogue). If
the raw ratio is noisy, ``method="exp_fit"`` derives ``f[t]`` from a smooth
``S[t] = A*exp(-t/tau) + C`` fit instead. The chosen method, reference frame, and
any fit params are recorded in ``bleach.json`` for provenance/reproducibility.

The volume for each timepoint is streamed one at a time via
``get_view(dataset, channel, t).compute()`` (``correct=False`` — raw), so factor
computation never materializes the 4D array and never recurses through the
correction path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional, Union

import numpy as np
from skimage.filters import threshold_otsu

from canmac.io.atomic import atomic_write_json, is_done, write_done

PathLike = Union[str, Path]

CHANNELS = ("candida", "macrophage")
DEFAULT_METHOD = "ratio_foreground_mean"
_FALLBACK_PERCENTILE = 99.0  # high-percentile fallback when Otsu is degenerate

# Per-channel correction policy (D-07; 01-04 human-verify checkpoint decision
# "correct macrophage only"). The QC decay plots showed the two channels behave
# oppositely: the macrophage foreground mean PHOTOBLEACHES (monotonic decay ->
# ratio-to-reference correction is applicable), whereas the candida foreground
# mean RISES over the timelapse (real biological growth: proliferation / hyphal
# biomass). A ratio-to-reference "correction" anchored at t0 would divide that
# real growth signal down to the near-empty t0 level, CORRUPTING the data — so it
# must NOT be applied on read for candida. Factors are still computed + stored for
# provenance/QC, but flagged ``apply: false``; the reader honours the flag.
APPLY_POLICY: dict[str, dict[str, Any]] = {
    "macrophage": {
        "apply": True,
        "reason": "photobleaching decay — ratio-to-reference foreground correction applicable",
    },
    "candida": {
        "apply": False,
        "reason": "rising foreground = biological growth, not photobleaching; correction not applicable",
    },
}


def should_apply(channel: str) -> bool:
    """Whether the stored bleach factor is APPLIED on read for ``channel`` (:data:`APPLY_POLICY`).

    Unknown channels default to ``True`` (correct) — known channels follow the
    checkpoint-decided policy (macrophage True, candida False).
    """
    return bool(APPLY_POLICY.get(channel, {}).get("apply", True))


def apply_reason(channel: str) -> str:
    """Human-readable reason for the :func:`should_apply` policy of ``channel``."""
    return str(APPLY_POLICY.get(channel, {}).get("reason", ""))


def foreground_mean(vol: Any) -> float:
    """Masked mean intensity over the foreground of ONE volume (never whole-volume).

    The store is big-endian ``>u2``; the volume is cast to native-order float so the
    threshold and mean compute on native floats (RESEARCH byte-order note). The
    foreground is the set of voxels above an Otsu threshold; if Otsu is degenerate
    on a (near-)constant/sparse frame it falls back to a high percentile — NEVER a
    fixed absolute cutoff (RESEARCH "Don't Hand-Roll": decaying dynamic range breaks
    fixed thresholds). Returns the mean of the foreground voxels.
    """
    v = np.asarray(vol, dtype=np.float32)  # >u2 -> native f4 (byte-order safe)
    try:
        thr = float(threshold_otsu(v))
    except Exception:
        thr = float(np.percentile(v, _FALLBACK_PERCENTILE))
    mask = v > thr
    if not mask.any():
        thr = float(np.percentile(v, _FALLBACK_PERCENTILE))
        mask = v > thr
    if not mask.any():  # last resort: fully constant frame
        return float(v.mean())
    return float(v[mask].mean())


def linear_slope(y: Any, x: Optional[Any] = None) -> float:
    """Least-squares linear-fit slope of ``y`` (vs ``x``, or the sample index)."""
    y = np.asarray(y, dtype=float)
    x = np.arange(len(y), dtype=float) if x is None else np.asarray(x, dtype=float)
    return float(np.polyfit(x, y, 1)[0])


def factors_from_series(S: Any, ref: int = 0) -> dict[int, float]:
    """Ratio-to-reference factors: ``f[t] = S[ref] / S[t]`` (``f[ref] == 1.0``).

    A non-positive ``S[t]`` (empty foreground) maps to ``1.0`` (no correction) so a
    degenerate frame never injects an inf/NaN into the stored factors.
    """
    S = [float(x) for x in S]
    sref = S[ref]
    return {t: (sref / s if s > 0 else 1.0) for t, s in enumerate(S)}


def _exp_fit_series(S: Any) -> list[float]:
    """Smooth ``S[t] = A*exp(-t/tau) + C`` fit of the raw foreground series.

    Used only when ``method="exp_fit"`` (D-07 discretion, for a noisy raw ratio).
    Falls back to the raw series if the non-linear fit does not converge.
    """
    from scipy.optimize import curve_fit

    S = np.asarray(S, dtype=float)
    t = np.arange(len(S), dtype=float)

    def model(tt, A, tau, C):
        return A * np.exp(-tt / tau) + C

    A0 = float(S[0] - S[-1])
    p0 = [A0 if A0 > 0 else float(S[0]), max(len(S) / 3.0, 1.0), float(S[-1])]
    try:
        popt, _ = curve_fit(model, t, S, p0=p0, maxfev=10000)
        return [float(model(tt, *popt)) for tt in t]
    except Exception:
        return [float(s) for s in S]


def compute_bleach_factors(
    dataset: str,
    channel: str,
    ref: int = 0,
    method: str = DEFAULT_METHOD,
    n_t: Optional[int] = None,
    volume_source: Optional[Callable[[int], Any]] = None,
) -> dict[str, Any]:
    """Compute per-timepoint foreground bleach factors for one (dataset, channel).

    Streams one raw ``(Z, Y, X)`` volume per timepoint (``get_view(..., t)`` with
    ``correct=False`` — never the 4D array, never the correction path), takes the
    foreground mean ``S[t]``, and derives multiplicative factors ``f[t]`` toward the
    reference frame ``ref`` (default t0). ``method`` is ``"ratio_foreground_mean"``
    (default) or ``"exp_fit"``.

    ``volume_source`` (a ``t -> ndarray`` callable) is injectable for tests; when
    omitted the real lazy reader is used. Returns a dict with ``channel``,
    ``method``, ``ref``, ``n_t``, ``factors`` ({t: f}) and ``S_raw`` ({t: S}) for QC.
    """
    if volume_source is None:
        from canmac.io.reader import get_view

        def volume_source(t: int):  # noqa: E306 — local raw streamer (correct defaults False)
            return get_view(dataset, channel, t).compute()

    if n_t is None:
        from canmac.io.manifest import get as get_dataset

        n_t = int(get_dataset(dataset)["dims"]["T"])

    S = [foreground_mean(volume_source(t)) for t in range(n_t)]

    if method == "exp_fit":
        factors = factors_from_series(_exp_fit_series(S), ref=ref)
    else:
        method = DEFAULT_METHOD
        factors = factors_from_series(S, ref=ref)

    return {
        "channel": channel,
        "method": method,
        "ref": ref,
        "n_t": n_t,
        "factors": {int(t): float(f) for t, f in factors.items()},
        "S_raw": {int(t): float(s) for t, s in enumerate(S)},
    }


def bleach_path(dataset: str, out_dir: str = "results/{roi}") -> str:
    """Path to ``bleach.json`` for ``dataset`` (``{roi}`` filled with the id)."""
    return str(Path(out_dir.format(roi=dataset)) / "bleach.json")


def save_bleach(
    dataset: str,
    channel: str,
    factors: dict[int, float],
    S_raw: dict[int, float],
    method: str,
    ref: int,
    params: Optional[dict[str, Any]] = None,
    apply: Optional[bool] = None,
    reason: Optional[str] = None,
    out_dir: str = "results/{roi}",
) -> str:
    """Atomically merge one channel's factors into ``results/{roi}/bleach.json``.

    Both channels share one ``bleach.json``, so an existing payload is read and the
    channel entry updated in place, then the whole record is re-written atomically
    (``atomic_write_json`` -> ``write_done`` LAST). The ``raw.zarr`` is never touched.
    Records ``method``, ``ref``, ``factors`` ({t: f}), the raw ``S_raw`` series, and
    any fit ``params`` per channel (provenance seed for OPS-03 reproducibility).

    ``apply`` records whether the stored factor should be APPLIED on read (D-07;
    01-04 checkpoint). When omitted it defaults to :func:`should_apply` for the
    channel (macrophage True, candida False), with ``reason`` from
    :func:`apply_reason`. The reader honours ``apply`` — a ``False`` channel keeps
    its factors for provenance/QC but is returned RAW by ``get_view(correct=True)``.
    """
    if apply is None:
        apply = should_apply(channel)
    if reason is None:
        reason = apply_reason(channel)
    path = Path(bleach_path(dataset, out_dir))
    if path.exists():
        payload = json.loads(path.read_text())
    else:
        payload = {"dataset": dataset, "channels": {}}
    payload.setdefault("channels", {})[channel] = {
        "method": method,
        "ref": int(ref),
        "apply": bool(apply),
        "reason": reason,
        "params": params or {},
        "factors": {str(int(t)): float(f) for t, f in factors.items()},
        "S_raw": {str(int(t)): float(s) for t, s in S_raw.items()},
    }
    atomic_write_json(str(path), payload)  # payload first ...
    write_done(str(path))  # ... sentinel LAST (atomicity, T-01-04-03)
    return str(path)


def load_factors(dataset: str, out_dir: str = "results/{roi}") -> dict[str, Any]:
    """Load the ``bleach.json`` record for ``dataset`` (raises if absent)."""
    path = Path(bleach_path(dataset, out_dir))
    if not path.exists():
        raise FileNotFoundError(f"no bleach.json for {dataset}: {path}")
    return json.loads(path.read_text())


def factor_for(dataset: str, channel: str, t: int, out_dir: str = "results/{roi}") -> float:
    """Return the stored bleach factor ``f[t]`` for ``(dataset, channel)``.

    Read by :func:`canmac.io.reader.get_view` when ``correct=True`` to apply the
    correction as a lazy scalar broadcast. JSON keys are strings — indexed by ``str(t)``.
    """
    payload = load_factors(dataset, out_dir)
    return float(payload["channels"][channel]["factors"][str(int(t))])


def applies_on_read(dataset: str, channel: str, out_dir: str = "results/{roi}") -> bool:
    """Whether ``bleach.json`` says the factor is applied on read for this channel.

    Reads the stored ``apply`` flag (defaults to ``True`` if a legacy record omits
    it). The reader gates ``get_view(correct=True)`` on this — a ``False`` channel
    (candida: growth, not bleaching) is returned RAW even when ``correct=True``.
    """
    payload = load_factors(dataset, out_dir)
    return bool(payload["channels"][channel].get("apply", True))


def is_bleach_done(dataset: str, out_dir: str = "results/{roi}") -> bool:
    """Whether ``bleach.json`` has its ``.done`` sentinel (safe to consume)."""
    return is_done(bleach_path(dataset, out_dir))
