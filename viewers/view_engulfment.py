#!/usr/bin/env python
"""View PROPOSED candida-in-macrophage containment in napari (3D) — read-only.

For ONE timepoint, loads both raw channels + both label stores, runs the containment
prototype (canmac.stages.engulfment), and opens napari layers so you can CONFIRM each
proposed engulfment/entrapment event by eye. Writes nothing.

Usage (VNC GPU desktop terminal):
    export PATH="$HOME/.pixi/bin:$PATH"
    cd /vast/scratch/users/kriel.j/monash_lsm
    pixi run python view_engulfment.py                 # newest timepoint with BOTH channels
    pixi run python view_engulfment.py --t 100
    pixi run python view_engulfment.py --t 100 --candida 3     # crop to one candida object
    pixi run python view_engulfment.py --list                  # which timepoints are ready
    pixi run python view_engulfment.py --t 100 --check         # table only, no GUI

napari layers:
    macrophage raw (green) · candida raw (magenta) · macrophage labels ·
    candida by PROPOSED class (1=free, 2=partial, 3=engulfed)   <- the layer to confirm

Nothing here is validated: containment fraction is geometry. Whether a pair is a real
engulfment event is your call (and true engulfment needs persistence over time, which the
per-timepoint prototype does not test).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent  # repo root (this script lives in viewers/)
os.chdir(REPO)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

CLASS_CODE = {"free": 1, "partial": 2, "engulfed": 3}


def ready_timepoints(dataset: str, candida_store: str | None = None) -> list[int]:
    """Timepoints whose BOTH channels are segmented (per-t .done sentinels present)."""
    from canmac.io.atomic import is_done

    mac_store = f"results/{dataset}/macrophage_labels.zarr"
    cand_store = f"results/{dataset}/{candida_store or 'candida_labels'}.zarr"
    out = []
    for t in range(120):
        m = f"{mac_store}/t{t:03d}"
        c = f"{cand_store}/t{t:03d}"
        if is_done(m) and is_done(c):
            out.append(t)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="View PROPOSED engulfment/containment in napari.")
    ap.add_argument("--dataset", default="ROI2", choices=["ROI2", "ROI7"])
    ap.add_argument("--t", type=int, default=None,
                    help="Timepoint (default: the latest one with both channels segmented).")
    ap.add_argument("--candida", type=int, default=None,
                    help="Crop the view to this candida label (+ margin) to inspect one pair.")
    ap.add_argument("--candida-store", default=None, dest="candida_store",
                    help="Alternate candida label store name (e.g. ms_localstd_masked).")
    ap.add_argument("--engulf-frac", type=float, default=0.9, dest="engulf_frac")
    ap.add_argument("--touch-frac", type=float, default=0.05, dest="touch_frac")
    ap.add_argument("--dilate", type=int, default=0,
                    help="Grow macrophages N voxels before testing (adhesion vs internalization).")
    ap.add_argument("--margin", type=int, default=20, help="Crop margin in voxels for --candida.")
    ap.add_argument("--list", action="store_true", help="List ready timepoints and exit.")
    ap.add_argument("--check", action="store_true", help="Print the table; do not open napari.")
    args = ap.parse_args()

    from canmac.io.reader import get_view
    from canmac.stages.engulfment import containment, load_labels

    ready = ready_timepoints(args.dataset, args.candida_store)
    if args.list or not ready:
        print(f"{args.dataset}: {len(ready)} timepoint(s) with BOTH channels segmented:")
        print("  ", ready if ready else "(none yet — segmentation arrays still running)")
        if args.list or not ready:
            return

    t = args.t if args.t is not None else ready[-1]
    if t not in ready:
        print(f"WARNING: t{t:03d} does not have both channels yet. Ready: {ready}")
        return

    mac_lab = load_labels(args.dataset, "macrophage", t)
    cand_lab = load_labels(args.dataset, "candida", t, store_name=args.candida_store)
    _pairs, per_c = containment(mac_lab, cand_lab, engulf_frac=args.engulf_frac,
                                touch_frac=args.touch_frac, dilate=args.dilate)
    n_e = int((per_c["PROPOSED_class"] == "engulfed").sum()) if len(per_c) else 0
    n_p = int((per_c["PROPOSED_class"] == "partial").sum()) if len(per_c) else 0
    print(f"{args.dataset} t{t:03d}: macrophages={int(mac_lab.max())} candida={len(per_c)} "
          f"-> PROPOSED engulfed={n_e} partial={n_p} free={len(per_c)-n_e-n_p}")
    if len(per_c):
        print(per_c.to_string(index=False))
    print("^ PROPOSED — confirm each pair visually. Per-timepoint only (no persistence test).")

    # class volume: recolor candida labels by their proposed class
    classv = np.zeros_like(cand_lab, dtype=np.uint8)
    for r in per_c.itertuples():
        classv[cand_lab == int(r.candida)] = CLASS_CODE[r.PROPOSED_class]

    mac_raw = np.ascontiguousarray(
        get_view(args.dataset, "macrophage", t, correct=True).compute()).astype(np.float32)
    cand_raw = np.ascontiguousarray(
        get_view(args.dataset, "candida", t, correct=False).compute()).astype(np.float32)

    sl = tuple(slice(None) for _ in range(3))
    if args.candida is not None:
        m = np.argwhere(cand_lab == args.candida)
        if len(m) == 0:
            print(f"candida label {args.candida} not present at t{t:03d}")
            return
        lo = np.maximum(m.min(0) - args.margin, 0)
        hi = np.minimum(m.max(0) + args.margin + 1, cand_lab.shape)
        sl = tuple(slice(int(a), int(b)) for a, b in zip(lo, hi))
        row = per_c[per_c["candida"] == args.candida]
        print(f"cropped to candida {args.candida}: z{sl[0].start}-{sl[0].stop} "
              f"y{sl[1].start}-{sl[1].stop} x{sl[2].start}-{sl[2].stop}")
        if len(row):
            print(row.to_string(index=False))

    if args.check:
        print("--check: skipping napari GUI.")
        return

    import napari

    from canmac.io.manifest import get

    try:
        vox = float(get(args.dataset)["voxel_um"])
    except Exception:
        vox = 0.14499219272808386
    scale = (vox, vox, vox)
    v = napari.Viewer(ndisplay=3)
    v.add_image(mac_raw[sl], name="macrophage raw", scale=scale, colormap="green",
                blending="additive")
    v.add_image(cand_raw[sl], name="candida raw", scale=scale, colormap="magenta",
                blending="additive")
    v.add_labels(mac_lab[sl], name=f"macrophage labels ({int(mac_lab.max())})", scale=scale,
                 opacity=0.35)
    v.add_labels(cand_lab[sl], name="candida labels", scale=scale, visible=False)
    v.add_labels(classv[sl], name="candida by PROPOSED class (1free 2partial 3engulfed)",
                 scale=scale)
    v.scale_bar.visible = True
    v.scale_bar.unit = "um"
    napari.run()


if __name__ == "__main__":
    main()
