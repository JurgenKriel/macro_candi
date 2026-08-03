#!/usr/bin/env python
"""View a segmentation result in napari (interactive 3D) — no Jupyter widgets needed.

Loads ONE timepoint of raw signal + its Cellpose-SAM instance labels and opens them
as overlaid napari layers, so you can scrub Z / rotate in 3D on a VNC GPU desktop.

Usage (from a VNC GPU desktop terminal):
    export PATH="$HOME/.pixi/bin:$PATH"
    cd /vast/scratch/users/kriel.j/monash_lsm
    pixi run python view_labels.py ROI2 macrophage 100
    pixi run python view_labels.py ROI2 candida 90

Options:
    --segment   Run Cellpose-SAM live for this timepoint if no saved labels exist
                (needs a CUDA GPU on the VM — fails loud otherwise).
    --check     Load raw+labels and print shapes, but do NOT open the napari GUI
                (headless verification; used to sanity-check without a display).

Bleach policy (D-05) matches the segmentation stage: macrophage is read
bleach-corrected, candida is read raw (its rise is real growth, not bleaching).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

# --- run from the repo root so `canmac` imports and relative results/ resolve,
#     regardless of the invoking CWD (the script lives at the repo root). ---
REPO = pathlib.Path(__file__).resolve().parent.parent  # repo root (this script lives in viewers/)
os.chdir(REPO)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402


def _load_labels(dataset: str, channel: str, t: int, store: str = None):
    """Return the saved uint label volume for (dataset, channel, t), or None if absent.

    `store` overrides the default `results/{dataset}/{channel}_labels.zarr` so any label
    store with a `t{NNN}` layout can be viewed (e.g. results/ROI2/candida_microsam.zarr)."""
    import zarr

    store = store or f"results/{dataset}/{channel}_labels.zarr"
    if not pathlib.Path(store).exists():
        return None
    root = zarr.open_group(store, mode="r")
    key = f"t{t:03d}"
    if key not in root:
        return None
    return root[key][:]


def _voxel_um(dataset: str) -> float:
    """Isotropic voxel size (µm) from the manifest — for physical-unit napari scale."""
    try:
        from canmac.io.manifest import get

        return float(get(dataset)["voxel_um"])
    except Exception:
        return 0.14499219272808386  # known ROI2/ROI7 isotropic voxel


def main() -> None:
    ap = argparse.ArgumentParser(description="View a segmentation timepoint in napari.")
    ap.add_argument("dataset", choices=["ROI2", "ROI7"])
    ap.add_argument("channel", choices=["macrophage", "candida"])
    ap.add_argument("t", type=int, help="Timepoint index (e.g. 100).")
    ap.add_argument("--store", default=None,
                    help="Override label store path (e.g. results/ROI2/candida_microsam.zarr).")
    ap.add_argument("--segment", action="store_true",
                    help="Run Cellpose-SAM live for this timepoint (needs CUDA). Implied if any "
                         "param override below is given.")
    ap.add_argument("--cellprob", type=float, default=None,
                    help="Override cellprob_threshold (live 3D knob; lower -> more/larger objects).")
    ap.add_argument("--flow3d-smooth", type=float, default=None, dest="flow3d_smooth",
                    help="Override flow3D_smooth (raise to reduce Z-fragmentation).")
    ap.add_argument("--min-size", type=int, default=None, dest="min_size",
                    help="Override min_size (voxels).")
    ap.add_argument("--batch-size", type=int, default=None, dest="batch_size",
                    help="Override batch_size (VRAM knob).")
    ap.add_argument("--save-params", action="store_true",
                    help="After a live segment, write the effective params back to "
                         "params/cpsam_{channel}.json.")
    ap.add_argument("--check", action="store_true",
                    help="Load and print shapes only; do not open the napari GUI.")
    args = ap.parse_args()

    from canmac.io.reader import get_view

    correct = args.channel == "macrophage"  # D-05: macrophage corrected, candida raw
    raw = np.ascontiguousarray(
        get_view(args.dataset, args.channel, args.t, correct=correct).compute()
    ).astype(np.float32)
    print(f"raw  {args.dataset}/{args.channel} t{args.t:03d}: shape={raw.shape} "
          f"dtype={raw.dtype} mean={raw.mean():.1f}")

    # Param overrides (any given) imply a live re-segment with those values.
    overrides = {k: v for k, v in (
        ("cellprob_threshold", args.cellprob),
        ("flow3D_smooth", args.flow3d_smooth),
        ("min_size", args.min_size),
        ("batch_size", args.batch_size),
    ) if v is not None}
    do_segment = args.segment or bool(overrides)

    if do_segment:
        import json

        from canmac.stages.segment import segment_timepoint

        params_path = f"params/cpsam_{args.channel}.json"
        params = json.load(open(params_path))
        params.update(overrides)
        print(f"segmenting {args.channel} t{args.t:03d} live (needs CUDA) with params={params}")
        labels = segment_timepoint(args.dataset, args.channel, args.t, params)
        if args.save_params:
            json.dump(params, open(params_path, "w"), indent=2)
            print(f"saved effective params -> {params_path}")
    else:
        labels = _load_labels(args.dataset, args.channel, args.t, store=args.store)

    if labels is None:
        print(f"NOTE: no saved labels at results/{args.dataset}/{args.channel}_labels.zarr"
              f"/t{args.t:03d}. Run the segmentation first, or pass --segment on a CUDA GPU. "
              f"Showing the raw volume only.")
        n_obj = 0
    else:
        n_obj = int(labels.max())
        print(f"labels: shape={labels.shape} n_objects={n_obj} "
              f"fg_voxels={int((labels > 0).sum())}")

    if args.check:
        print("--check: skipping napari GUI.")
        return

    import napari

    vox = _voxel_um(args.dataset)
    scale = (vox, vox, vox)  # isotropic 0.145 µm -> napari shows physical units
    viewer = napari.Viewer(ndisplay=3)
    viewer.add_image(raw, name=f"{args.channel} raw t{args.t:03d}", scale=scale,
                     colormap="green" if args.channel == "macrophage" else "magenta",
                     blending="additive")
    if labels is not None:
        viewer.add_labels(labels, name=f"{args.channel} labels ({n_obj})", scale=scale)
    viewer.scale_bar.visible = True
    viewer.scale_bar.unit = "um"
    napari.run()


if __name__ == "__main__":
    main()
