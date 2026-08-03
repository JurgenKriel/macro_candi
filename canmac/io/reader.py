"""Lazy streaming reader — the single disk choke-point for pixel access (D-04, IO-01).

``get_view(dataset, channel[, t])`` returns a LAZY, channel-selected dask array
over the level-0 OME-Zarr store. The whole T x C x Z x Y x X array is never
materialized: one (t, c) uint16 volume is ~89 MB (301*401*369*2 B for ROI2)
whereas the full 4D array is ~32 GB. The contract is that callers materialize at
most ONE timepoint at a time; this reader never realizes the 4D array, which
would OOM (RESEARCH Pitfall 5). Wrapping the only disk read here in one lazy
reader is the anti-OOM guarantee for the entire pipeline.

Channel selection is by omero color via ``resolve_channel`` (never by index), so
the lysed channel (FF00FF) can never leak into a read (D-03).

The store is Zarr v2, big-endian ``>u2``, ``dimension_separator "/"``;
``dask.array.from_zarr`` reads it transparently as 2-byte unsigned int (retires
RESEARCH assumption A1). No pixels are read until the caller materializes a view.
"""

from __future__ import annotations

import logging
from typing import Optional

import dask.array as da
import numpy as np

from .channels import resolve_channel
from .manifest import get as get_dataset

_LOG = logging.getLogger(__name__)


def _store_path(rec: dict) -> str:
    """Absolute store path from a validated manifest record (falls back to zarr_path)."""
    return str(rec.get("_store") or rec["zarr_path"])


def _level0(dataset_id: str):
    """Return the lazy 5D (T,C,Z,Y,X) level-0 dask array and its manifest record.

    Fails loud if the on-disk level-0 shape drifts from the validated manifest
    dims (a wrong shape means the store or manifest is corrupt — never continue).
    """
    rec = get_dataset(dataset_id)
    component = rec.get("component", "0/0")
    arr = da.from_zarr(_store_path(rec), component=component)  # lazy — no pixels read
    dims = rec["dims"]
    expected = (dims["T"], dims["C"], dims["Z"], dims["Y"], dims["X"])
    if arr.shape != expected:
        raise ValueError(
            f"{dataset_id}: level-0 shape {arr.shape} disagrees with manifest dims {expected}"
        )
    return arr, rec


def get_view(dataset: str, channel: str, t: Optional[int] = None, correct: bool = False):
    """Return a lazy, color-selected dask array for ``dataset``/``channel``.

    ``channel`` is resolved by omero color ("candida" -> FF0000, "macrophage" ->
    00FF00); the lysed channel is never exposed. With ``t=None`` the result is the
    lazy (T, Z, Y, X) stack; with an integer ``t`` it is the lazy (Z, Y, X) volume
    for that timepoint. The return value is ALWAYS a dask array — the caller
    materializes at most one timepoint (never the 4D array).

    When ``correct=True`` the stored photobleaching factor (``bleach.json`` via
    :mod:`canmac.io.bleach`) is applied as a LAZY scalar broadcast: for a single
    ``t`` the ``(Z,Y,X)`` volume is multiplied by the scalar ``f[t]``; for
    ``t=None`` the per-timepoint factor vector is broadcast along the T axis. The
    multiply stays lazy (dask * scalar/vector) — it costs nothing until the caller
    computes one timepoint, and the ~32 GB ``raw.zarr`` is never rewritten (D-07).
    ``correct=False`` returns the raw lazy view, so bleach-factor computation can
    stream raw frames through this reader without recursing into the correction.

    Per-channel policy (01-04 checkpoint "correct macrophage only"): the correction
    is applied ONLY to channels whose ``bleach.json`` record has ``apply: true``
    (macrophage). For a channel flagged ``apply: false`` (candida — its rising
    foreground is real biological growth, not photobleaching), ``correct=True`` is
    an explicit NO-OP: the RAW view is returned so candida's growth signal is never
    divided away, and the skip is logged (never a silent pretend-correction).
    """
    arr, rec = _level0(dataset)
    zattrs = f"{_store_path(rec)}/0/.zattrs"
    c = resolve_channel(zattrs, channel)          # color-resolved C index, never lysed
    view = arr[:, c] if t is None else arr[t, c]  # lazy (T,Z,Y,X) or (Z,Y,X)
    if not correct:
        return view                                # STILL LAZY — caller materializes one t

    # Lazy bleach correction (imported here to avoid a module-level import cycle:
    # bleach.py streams raw frames back through get_view(..., correct=False)).
    from .bleach import load_factors

    payload = load_factors(dataset)
    ch_rec = payload["channels"][channel]
    if not ch_rec.get("apply", True):
        # Correction intentionally NOT applied for this channel (e.g. candida
        # growth). Record the skip and return the raw view unchanged.
        _LOG.info(
            "bleach: correction intentionally skipped for %s/%s (%s) — returning raw view",
            dataset, channel, ch_rec.get("reason", "apply=false"),
        )
        return view                                # RAW — factor never applied
    factors = ch_rec["factors"]                    # {str(t): f}
    if t is not None:
        return view * float(factors[str(int(t))])  # (Z,Y,X) * float — STILL LAZY
    # t is None: broadcast the per-t factor vector along the T axis of (T,Z,Y,X).
    n_t = view.shape[0]
    fvec = np.array([factors[str(i)] for i in range(n_t)], dtype=float)
    return view * fvec[:, None, None, None]        # (T,Z,Y,X) * (T,1,1,1) — STILL LAZY
