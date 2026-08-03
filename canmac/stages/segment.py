"""Phase-3 GPU segmentation stage — one calibrated timepoint -> 3D instance labels.

This is the load-bearing correctness core of the segmentation phase. It turns ONE
streamed ``(Z,Y,X)`` timepoint volume into a 3D instance-label store plus an
object-census row, reusing the verified Phase-1 foundation (``get_view``,
``canmac.io.atomic``) and the Cellpose-SAM 4.2.1.1 API verified in 03-RESEARCH.

Decisions baked in (03-CONTEXT D-01..D-08):

* D-01: instance labels come from Cellpose-SAM (cpsam), NEVER threshold +
  connected-components (which would merge touching macrophages, failing SC2).
* D-02: native-3D isotropic path — ``do_3D=True``, ``anisotropy=1.0`` (0.145 um
  isotropic voxels), NOT 2D-stitch. ``z_axis=0`` is REQUIRED because ROI2 has
  Y=401 > Z=301, so cellpose's axis auto-guess mis-assigns the Z axis -> garbage
  flows. Cellpose 4 is size-invariant, so no object size is ever pinned.
* D-03: fail loud when CUDA is absent — a silent CPU fallback is ~100x slower and
  silently burns the SLURM walltime. ``_load_model`` also rejects the P100
  (12 GB) node (RESEARCH Pitfall 1) — the heterogeneous ``gpuq`` mixes P100/A30/A100.
* D-04: weights are pre-staged at an ABSOLUTE path (``WEIGHTS``) so the internet-less
  GPU node never triggers a runtime download that would hang the job.
* D-05: the macrophage channel is read bleach-CORRECTED; candida is read RAW (its
  rising foreground is real biological growth, not photobleaching). Expressed as
  ``correct=(channel == "macrophage")`` — candida + correct=True is a documented
  NO-OP in the reader, so this one expression cleanly carries the policy.
* D-06: label volumes persist to ``results/{roi}/{channel}_labels.zarr`` — a Zarr
  GROUP with one ``t{NNN}`` uint16 array per timepoint (one timepoint = one logical
  restart unit). The read-only ~32 GB ``raw.zarr`` is NEVER mutated.
* D-08: every write routes through :mod:`canmac.io.atomic`; the per-timepoint
  ``.done`` sentinel is written LAST, so a SLURM kill never leaves a
  present-but-incomplete label array that a re-run would treat as done. Re-runs
  skip completed timepoints (restart-safe).

The big-endian landmine (RESEARCH Pitfall 2, mitigated at bleach.py:89): the store
is big-endian ``>u2``; every volume is ``np.ascontiguousarray(vol).astype(np.float32)``
BEFORE it reaches torch, or the masks come back garbage.

Candida here is FOREGROUND only — the yeast-vs-hyphae morphology split is Phase 4.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np

from canmac.io.atomic import atomic_write_json, is_done, write_done
from canmac.io.reader import get_view

_LOG = logging.getLogger(__name__)

# Pre-staged Cellpose-SAM weights (D-04): absolute path -> offline-safe on the
# internet-less GPU node (never a runtime download).
WEIGHTS = os.environ.get("CANMAC_CPSAM_WEIGHTS",
                        os.path.join(os.environ.get("CELLPOSE_LOCAL_MODELS_PATH", ""), "cpsam"))

# Module-level model cache: the 1.2 GB weights load ONCE per process (Pattern 1);
# re-loading per timepoint would waste GPU time.
_MODEL: Optional[Any] = None


def _load_model() -> Any:
    """Load the Cellpose-SAM model once, failing loud when no GPU is present (D-03).

    Asserts ``torch.cuda.is_available()`` BEFORE importing cellpose so the guard
    fires cheaply, then rejects the P100 node (RESEARCH Pitfall 1) before loading
    the weights. The loaded model is cached in a module global so subsequent calls
    in the same process reuse it.
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    import torch

    # Fail loud — refuse a silent CPU fallback that would burn the SLURM walltime.
    assert torch.cuda.is_available(), "CUDA not available — refusing silent CPU fallback (D-03)"

    device_name = torch.cuda.get_device_name(0)
    _LOG.info("segment: CUDA device = %s", device_name)
    # The heterogeneous gpuq mixes P100 (12 GB) / A30 / A100; the P100 OOMs on the
    # cpsam transformer backbone. Fail loud rather than crash mid-run (Pitfall 1).
    assert "P100" not in device_name, (
        f"refusing to run on {device_name!r} (P100 OOMs on cpsam) — request an A30/A100"
    )

    from cellpose import models

    _MODEL = models.CellposeModel(gpu=True, pretrained_model=WEIGHTS)  # loads ONCE
    return _MODEL


def preprocess_volume(
    vol: np.ndarray,
    method: str = "none",
    tophat_radius: int = 10,
    dog_low: float = 1.0,
    dog_high: float = 6.0,
) -> np.ndarray:
    """Suppress the diffuse PSF/out-of-focus 'halo' BEFORE Cellpose sees it (candida).

    The candida fluorescence carries a broad low-intensity halo; Cellpose-SAM otherwise
    grows the mask into it, yielding round 'blobby' instances that would misclassify as
    yeast in the Phase-4 shape split. Applied per-(t,c) volume before eval so the batch
    and the tuning notebook de-halo identically — read from ``params['preprocess']`` in
    :func:`segment_timepoint`.

    method:
      * ``"none"``   — identity (default; macrophage needs no de-halo).
      * ``"tophat"`` — 3D white top-hat (``skimage.morphology.white_tophat``, ``ball``
        footprint of radius ``tophat_radius`` voxels): removes the smooth halo/background
        larger than the footprint, keeps compact bright structure. Footprint ~a bit larger
        than a yeast cell (0.145 µm voxel -> ~10-15). NOTE: a large 3D ball is CPU-heavy
        (grey opening) — fine per-timepoint, watch cost in the 120-frame batch.
      * ``"dog"``    — difference-of-Gaussians band-pass
        (``skimage.filters.difference_of_gaussians(vol, dog_low, dog_high)``): removes the
        broad low-frequency glow (``dog_high``) and fine noise (``dog_low``). Fast (Gaussian).
    Returns float32 clipped to >= 0 (Cellpose normalizes internally).
    """
    if method in (None, "none"):
        return vol
    if method == "tophat":
        from skimage.morphology import ball, white_tophat

        out = white_tophat(vol, footprint=ball(int(tophat_radius)))
    elif method == "dog":
        from skimage.filters import difference_of_gaussians

        out = difference_of_gaussians(vol, float(dog_low), float(dog_high))
    else:
        raise ValueError(f"unknown preprocess method {method!r} (none|tophat|dog)")
    return np.clip(out, 0, None).astype(np.float32)


def normalize_volume(
    vol: np.ndarray,
    method: str = "none",
    sigma: float = 12.0,
    clip: float = 5.0,
    clahe_clip: float = 0.01,
    kernel_frac: float = 0.125,
    mask_percentile: Optional[float] = None,
) -> np.ndarray:
    """Flatten NON-UNIFORM intensity *within* structures before segmentation.

    The candida/hyphae signal varies along a single filament (some stretches much dimmer),
    which breaks every segmentation strategy: a global threshold cuts the structure at the
    dim parts, and SAM/Cellpose confidence collapses there. This normalizes LOCAL contrast
    so a dim stretch of hypha looks like a bright one. Distinct from
    :func:`preprocess_volume` (which removes the diffuse halo AROUND structures).

    method:
      * ``"none"``     — identity.
      * ``"localstd"`` — local mean/std normalization: ``(I - G_sigma(I)) / (sqrt(G_sigma(I^2)
        - G_sigma(I)^2) + eps)``, clipped to +/-``clip``. Fast (3 Gaussians), the standard
        uneven-illumination fix. ``sigma`` should be a few times the structure width
        (~12 voxels ≈ 1.7 µm at 0.145 µm/voxel).
      * ``"clahe"``    — 3D contrast-limited adaptive histogram equalization
        (``skimage.exposure.equalize_adapthist``, kernel = ``kernel_frac`` of each axis).
        Strong local contrast; slower and can amplify background noise.
    Returns float32.
    """
    if method in (None, "none"):
        return vol
    v = vol.astype(np.float32, copy=False)
    if method == "localstd":
        from scipy.ndimage import gaussian_filter

        # MEASURED: unmasked localstd amplifies background noise (Otsu then grabs ~40% of the
        # volume). mask_percentile (e.g. 98) restricts the enhancement to a softly-dilated
        # signal mask so background stays suppressed. Strongly recommended for candida.
        keep = None
        if mask_percentile is not None:
            sig = v > np.percentile(v, mask_percentile)
            keep = gaussian_filter(sig.astype(np.float32), 4) > 0.05
        mu = gaussian_filter(v, sigma)
        var = np.maximum(gaussian_filter(v * v, sigma) - mu * mu, 0.0)
        out = np.clip((v - mu) / (np.sqrt(var) + 1e-6), -clip, clip)
        if keep is not None:
            out = np.where(keep, out, out.min())
        return out.astype(np.float32)
    if method == "clahe":
        from skimage.exposure import equalize_adapthist

        lo, hi = float(v.min()), float(v.max())
        vn = (v - lo) / (hi - lo + 1e-12)  # equalize_adapthist needs [0,1]
        kernel = tuple(max(4, int(s * kernel_frac)) for s in v.shape)
        return equalize_adapthist(vn, kernel_size=kernel, clip_limit=clahe_clip).astype(np.float32)
    raise ValueError(f"unknown normalize method {method!r} (none|localstd|clahe)")


def segment_timepoint(
    dataset: str,
    channel: str,
    t: int,
    params: dict,
    model: Optional[Any] = None,
) -> np.ndarray:
    """Segment ONE timepoint volume into a uint16 3D instance-label array.

    Streams a single ``(Z,Y,X)`` volume via :func:`canmac.io.reader.get_view` with
    ``correct=(channel == "macrophage")`` (D-05: macrophage corrected, candida raw),
    materializes exactly that one timepoint, casts the big-endian ``>u2`` payload to
    native-order C-contiguous float32 (Pitfall 2), and runs Cellpose-SAM with the
    locked isotropic native-3D eval config (D-02).

    ``model`` may be injected (tests / a pre-loaded batch driver) to bypass the GPU
    guard + weight load; otherwise :func:`_load_model` supplies the cached model.
    Returns instance labels as ``uint16`` (per-frame counts are far below 65535).
    """
    if model is None:
        model = _load_model()

    correct = channel == "macrophage"  # D-05 bleach policy (candida raw = NO-OP)
    vol = get_view(dataset, channel, t, correct=correct).compute()  # ONE (Z,Y,X) >u2
    # Big-endian >u2 -> native float32, C-contiguous, BEFORE torch (Pitfall 2).
    vol = np.ascontiguousarray(vol).astype(np.float32)
    # Optional halo suppression BEFORE eval (candida); read from params so the batch matches.
    vol = preprocess_volume(
        vol,
        params.get("preprocess", "none"),
        tophat_radius=params.get("tophat_radius", 10),
        dog_low=params.get("dog_low", 1.0),
        dog_high=params.get("dog_high", 6.0),
    )

    labels, _flows, _styles = model.eval(
        vol,
        do_3D=True,          # native-3D path (NOT 2D-stitch) — D-02
        z_axis=0,            # REQUIRED: (Z,Y,X); ROI2 Y=401 > Z=301 breaks auto-axis
        channel_axis=None,   # grayscale; cellpose replicates to 3ch internally
        anisotropy=1.0,      # isotropic 0.145 um — D-02
        cellprob_threshold=params["cellprob_threshold"],  # live 3D knob
        flow3D_smooth=params.get("flow3D_smooth", 0),     # raise to reduce Z-fragmentation
        min_size=params.get("min_size", 15),              # in voxels
        batch_size=params.get("batch_size", 8),           # VRAM knob
        normalize=True,      # default 1/99 percentile, norm3D over the stack
    )
    # NOTE: no object-size arg is set — Cellpose 4 is size-invariant (D-02).
    # `flow_threshold` is inert in do_3D; `channels=` is deprecated. Neither is passed.

    # Free the per-frame VRAM (Pitfall 5) — guarded so the CPU test path is a no-op.
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # pragma: no cover - torch always importable in the env
        pass

    return labels.astype(np.uint16)


def write_labels(store_path: str, t: int, labels: np.ndarray) -> None:
    """Persist ONE timepoint's uint16 label volume into ``store_path`` atomically.

    Layout (D-06): a single Zarr GROUP per store with one ``t{NNN}`` array per
    timepoint, dtype ``uint16``, chunked whole-volume ``(Z,Y,X)`` — one timepoint is
    one logical restart unit. The per-timepoint ``.done`` sentinel
    (``{store_path}/t{NNN}.done``) is written LAST (D-08): a SLURM kill mid-write
    leaves no sentinel, so a re-run overwrites the partial array; a completed
    timepoint is skipped. Writes only under ``results/{roi}/`` — never the read-only
    ``raw.zarr``.
    """
    import zarr

    t_marker = f"{store_path}/t{t:03d}"  # per-timepoint sentinel path
    if is_done(t_marker):
        return  # restart skip (D-08) — this timepoint already completed

    labels = np.ascontiguousarray(labels).astype(np.uint16)
    root = zarr.open_group(store_path, mode="a")  # store persists; per-t arrays added
    arr = root.create_array(
        f"t{t:03d}",
        shape=labels.shape,
        dtype="uint16",
        chunks=labels.shape,  # whole-volume chunk: one timepoint = one chunk
        overwrite=True,
    )
    arr[:] = labels
    write_done(t_marker)  # LAST — sentinel only after the payload is durable


def census_row(dataset: str, channel: str, t: int, labels: np.ndarray) -> dict:
    """Return the fixed-schema object-census row for ONE timepoint (SEG-04).

    ``n_objects`` uses ``int(labels.max())`` — Cellpose returns contiguous IDs
    ``1..N`` (RESEARCH line 244), so the max IS the object count. ``fg_voxels`` is
    the foreground voxel count, used to distinguish a genuinely empty early frame
    from a threshold artifact (Pitfall 4).
    """
    return {
        "dataset": dataset,
        "channel": channel,
        "t": int(t),
        "n_objects": int(labels.max()),
        "fg_voxels": int((labels > 0).sum()),
    }


def write_census_shard(
    out_dir: str, dataset: str, channel: str, t: int, labels: np.ndarray
) -> str:
    """Atomically write ONE per-(channel,t) census shard under ``{out_dir}/census/``.

    Per-task JSON shards avoid the append race a shared CSV would suffer under a
    SLURM ``--array`` (RESEARCH Open Q3): each task writes exactly its own
    ``{channel}_t{NNN}.json`` (+ ``.done`` last); :func:`finalize_census` concats
    them afterwards. ``out_dir`` is the already-resolved ``results/{roi}`` dir.
    """
    row = census_row(dataset, channel, t, labels)
    shard = Path(out_dir) / "census" / f"{channel}_t{t:03d}.json"
    atomic_write_json(shard, row)
    write_done(str(shard))  # LAST
    return str(shard)


def finalize_census(out_dir: str, dataset: str) -> str:
    """Concat all per-(channel,t) census shards into ``{out_dir}/census.csv``.

    Reads every ``{out_dir}/census/*.json`` shard, builds a pandas DataFrame with
    columns ``[dataset, channel, t, n_objects, fg_voxels]`` sorted by
    ``(channel, t)``, and atomically writes ``{out_dir}/census.csv``. ``out_dir``
    may carry a ``{roi}`` placeholder (filled with ``dataset``).
    """
    import pandas as pd

    from canmac.io.atomic import atomic_write_text

    resolved = out_dir.format(roi=dataset) if "{roi}" in out_dir else out_dir
    census_dir = Path(resolved) / "census"
    rows = []
    if census_dir.exists():
        import json

        for shard in sorted(census_dir.glob("*.json")):
            with open(shard) as f:
                rows.append(json.load(f))

    cols = ["dataset", "channel", "t", "n_objects", "fg_voxels"]
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df = df.sort_values(["channel", "t"]).reset_index(drop=True)
    out_csv = Path(resolved) / "census.csv"
    atomic_write_text(str(out_csv), df.to_csv(index=False))
    return str(out_csv)


def main() -> None:
    """Segment ONE timepoint (a SLURM-array unit) or ``--finalize`` the census.

    Mirrors the Phase-1 stage CLI shape (``ingest.py``) but targets a single
    ``(dataset, channel, timepoint)`` — the D-02/D-08 restart unit — so it drops
    into a SLURM ``--array`` over timepoints. Fail-loud input validation (V5,
    T-03-01-04): argparse ``choices`` gate dataset/channel; ``--t`` is bounds-checked
    against the manifest ``dims['T']``; the params sidecar is JSON-validated (must
    carry ``cellprob_threshold``) before the GPU is touched. Completed timepoints
    (``.done`` sentinel present) are skipped (restart-safe). ``--finalize`` concats
    the per-task census shards into ``census.csv`` after the array completes.
    """
    import json

    from canmac.io.manifest import get

    parser = argparse.ArgumentParser(
        description="Phase-3 segmentation: one timepoint -> 3D instance labels + census row."
    )
    parser.add_argument("--dataset", required=True, choices=["ROI2", "ROI7"])
    parser.add_argument("--channel", choices=["macrophage", "candida"])
    parser.add_argument("--t", type=int, help="Timepoint index (e.g. $SLURM_ARRAY_TASK_ID).")
    parser.add_argument(
        "--params", default=None, help="Params JSON (default: params/cpsam_{channel}.json)."
    )
    parser.add_argument("--out-dir", default="results/{roi}", help="Output dir template.")
    parser.add_argument("--manifest", default=None, help="Path to manifest.yaml.")
    parser.add_argument(
        "--finalize", action="store_true", help="Concat census shards into census.csv and exit."
    )
    args = parser.parse_args()

    resolved_out = (
        args.out_dir.format(roi=args.dataset) if "{roi}" in args.out_dir else args.out_dir
    )

    if args.finalize:
        out_csv = finalize_census(args.out_dir, args.dataset)
        _LOG.info("census finalized -> %s", out_csv)
        return

    # Per-timepoint mode requires --channel and --t.
    if args.channel is None or args.t is None:
        parser.error("--channel and --t are required unless --finalize is given")

    # Fail-loud --t bounds check against manifest dims (D-08 / V5 input validation).
    record = get(args.dataset, args.manifest)
    n_t = record["dims"]["T"]
    if not (0 <= args.t < n_t):
        parser.error(f"--t {args.t} out of range [0,{n_t}) for {args.dataset}")

    # Load + validate the params sidecar BEFORE touching the GPU (fail-loud).
    params_path = args.params or f"params/cpsam_{args.channel}.json"
    with open(params_path) as f:
        params = json.load(f)
    if "cellprob_threshold" not in params:
        parser.error(f"params {params_path} missing required key 'cellprob_threshold'")

    store_path = f"{resolved_out}/{args.channel}_labels.zarr"
    if is_done(f"{store_path}/t{args.t:03d}"):  # restart skip (D-08)
        _LOG.info("t%03d already done for %s/%s — skipping", args.t, args.dataset, args.channel)
        return

    labels = segment_timepoint(args.dataset, args.channel, args.t, params)
    write_labels(store_path, args.t, labels)
    write_census_shard(resolved_out, args.dataset, args.channel, args.t, labels)
    _LOG.info(
        "segmented %s/%s t%03d -> %d objects",
        args.dataset, args.channel, args.t, int(labels.max()),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
