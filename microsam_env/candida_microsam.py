#!/usr/bin/env python
"""micro-sam (vit_l_lm) 3D automatic segmentation of the candida channel.

Runs in the ISOLATED microsam_env (its own conda pytorch-gpu). Reads ONE candida
timepoint via the main pipeline's reader (get_view, raw — candida is read raw per D-05),
runs micro-sam automatic instance segmentation in 3D, and writes the masks to a SEPARATE
store `results/{roi}/candida_microsam.zarr` — it never touches the Cellpose
`candida_labels.zarr` or the threshold prototype.

micro-sam is a SAM-based, intensity-robust segmenter (vs a global threshold), so it should
handle the NON-UNIFORM candida fluorescence that broke Otsu thresholding. Results are
PROPOSED — confirm visually in napari before treating them as correct.

Run on a GPU node (real CUDA). On the GPU-less login node, use --check (CPU, small crop)
and prefix pixi with CONDA_OVERRIDE_CUDA=12.4 (see segment_candida_microsam.sh).

Usage:
    pixi run python candida_microsam.py --t 90                      # GPU, full volume
    CONDA_OVERRIDE_CUDA=12.4 pixi run python candida_microsam.py --t 90 --check   # CPU crop smoke
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

# repo root = parent of microsam_env/ (so canmac imports + relative results/ resolve)
REPO = pathlib.Path(os.environ.get("CANMAC_REPO", pathlib.Path(__file__).resolve().parent.parent))
os.chdir(REPO)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402


def _write_masks(dataset: str, t: int, labels: np.ndarray, store="results/{roi}/candida_microsam.zarr"):
    """Atomic per-timepoint write to a SEPARATE store (never the Cellpose one)."""
    import zarr

    from canmac.io.atomic import is_done, write_done

    store = store.format(roi=dataset)
    marker = f"{store}/t{t:03d}"
    root = zarr.open_group(store, mode="a")
    arr = root.create_array(f"t{t:03d}", shape=labels.shape, dtype="uint16",
                            chunks=labels.shape, overwrite=True)
    arr[:] = np.ascontiguousarray(labels).astype(np.uint16)
    write_done(marker)  # sentinel LAST
    return store


def main() -> None:
    ap = argparse.ArgumentParser(description="micro-sam vit_l_lm 3D candida segmentation (PROPOSED).")
    ap.add_argument("--t", type=int, required=True, help="Timepoint index (e.g. 90).")
    ap.add_argument("--dataset", default="ROI2", choices=["ROI2", "ROI7"])
    ap.add_argument("--model", default="vit_l_lm")
    ap.add_argument("--mode", default="ais", choices=["ais", "amg"], help="ais = decoder AIS (lm models).")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--gap-closing", type=int, default=2, dest="gap_closing",
                    help="Close small Z gaps when merging 2D masks into 3D (helps broken hyphae).")
    ap.add_argument("--min-z-extent", type=int, default=2, dest="min_z_extent",
                    help="Drop objects spanning fewer than this many Z slices.")
    ap.add_argument("--preprocess", default="none", choices=["none","dog","tophat"],
                    help="Halo suppression AROUND structures.")
    ap.add_argument("--normalize", default="clahe", choices=["none","clahe","localstd"],
                    help="Local-contrast normalization WITHIN structures (dim-hyphae fix).")
    ap.add_argument("--norm-mask-percentile", type=float, default=98.0, dest="norm_mask_percentile",
                    help="localstd only: restrict enhancement to signal (avoids noise blowup).")
    ap.add_argument("--clahe-clip", type=float, default=0.01, dest="clahe_clip")
    ap.add_argument("--foreground-threshold", type=float, default=None, dest="foreground_threshold",
                    help="AIS: raise (0.6-0.8) to drop non-specific masks.")
    ap.add_argument("--min-size", type=int, default=None, dest="min_size")
    ap.add_argument("--z-start", type=int, default=0, dest="z_start")
    ap.add_argument("--z-stop", type=int, default=None, dest="z_stop")
    ap.add_argument("--batch-size", type=int, default=1, dest="batch_size",
                    help="SAM embedding VRAM knob — keep at 1 on the A30.")
    ap.add_argument("--out-name", default="candida_microsam", dest="out_name",
                    help="Store name under results/{roi}/ (use a new name to avoid overwriting).")
    ap.add_argument("--check", action="store_true",
                    help="CPU smoke on a small crop; does not write the full store.")
    args = ap.parse_args()

    # Direct zarr read (NOT get_view/dask): this env has old dask 2023.3.0 + zarr 3.1.5 whose
    # from_zarr passes read_only= that zarr rejects. Read the (t, candida) volume straight from
    # the store; channel resolved BY COLOR via canmac (D-03), candida is read RAW (D-05).
    import zarr

    from canmac.io.channels import resolve_channel
    from canmac.io.manifest import get

    rec = get(args.dataset)
    store = rec.get("_store") or rec["zarr_path"]
    comp = rec.get("component", "0/0")
    c_idx = resolve_channel(f"{store}/0/.zattrs", "candida")  # by omero color, never index
    root = zarr.open(store, mode="r")
    vol = np.ascontiguousarray(root[comp][args.t, c_idx]).astype(np.float32)  # (Z,Y,X) candida raw
    vol = vol[args.z_start:args.z_stop]
    from canmac.stages.segment import normalize_volume, preprocess_volume
    vol = preprocess_volume(vol, args.preprocess)
    vol = normalize_volume(vol, args.normalize, clahe_clip=args.clahe_clip,
                           mask_percentile=(args.norm_mask_percentile if args.normalize=="localstd" else None))
    print(f"  after preprocess={args.preprocess} normalize={args.normalize}: "
          f"shape={vol.shape} range=[{vol.min():.2f},{vol.max():.2f}]")
    device = "cpu" if args.check else args.device
    if args.check:
        z0 = vol.shape[0] // 2 - 12
        vol = vol[z0:z0 + 24, :96, :96]  # small crop so CPU AIS is quick
    print(f"{args.dataset}/candida t{args.t:03d}: vol {vol.shape} dtype={vol.dtype} "
          f"mean={vol.mean():.1f} | model={args.model} mode={args.mode} device={device}")

    from micro_sam.automatic_segmentation import get_predictor_and_segmenter
    from micro_sam.multi_dimensional_segmentation import automatic_3d_segmentation

    predictor, segmenter = get_predictor_and_segmenter(
        model_type=args.model, device=device, segmentation_mode=args.mode)
    gkw = {}
    if args.foreground_threshold is not None: gkw["foreground_threshold"] = args.foreground_threshold
    if args.min_size is not None: gkw["min_size"] = args.min_size
    labels = automatic_3d_segmentation(
        volume=vol, predictor=predictor, segmentor=segmenter, batch_size=args.batch_size,
        gap_closing=args.gap_closing, min_z_extent=args.min_z_extent, verbose=True, **gkw)
    n = int(labels.max())
    print(f"PROPOSED micro-sam labels: n_objects={n} fg_voxels={int((labels > 0).sum())} "
          f"(confirm visually — NOT validated)")

    if args.check:
        print("--check: crop only, store not written.")
        return
    store = _write_masks(args.dataset, args.t, labels, store="results/{roi}/"+args.out_name+".zarr")
    print(f"wrote {store}/t{args.t:03d}  (separate from candida_labels.zarr — Cellpose untouched)")


if __name__ == "__main__":
    main()
