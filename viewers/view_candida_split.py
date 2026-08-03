#!/usr/bin/env python
"""View the candida threshold -> skeleton -> graph split in napari (PROPOSED, read-only).

Recomputes the prototype split for ONE timepoint and opens it as napari layers on a VNC
GPU desktop, so you can confirm the yeast/hyphae proposal in 3D. Writes NOTHING to disk —
it does not touch results/ROI2/candida_labels.zarr (the Cellpose result) or anything else.

Usage (VNC GPU desktop terminal):
    export PATH="$HOME/.pixi/bin:$PATH"
    cd /vast/scratch/users/kriel.j/monash_lsm
    pixi run python view_candida_split.py 90 --preprocess dog
    pixi run python view_candida_split.py 90 --preprocess tophat --tophat-radius 12 --len-um-hyphae 4

napari layers:
    raw candida (image) · threshold (labels) · skeleton (image) ·
    PROPOSED class (labels: 1=yeast, 2=hyphae)  <- the layer to confirm

--check   compute + print the PROPOSED per-object table, no napari GUI (headless).
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


def compute_split(dataset, t, preprocess, tophat_radius, dog_low, dog_high, min_size, len_um_hyphae, vox):
    """Return (raw, thr, skel, class_vol, table) — all in memory, nothing written."""
    import pandas as pd
    from skimage.filters import threshold_otsu
    from skimage.measure import label as cc_label
    from skimage.measure import regionprops
    from skimage.morphology import remove_small_objects, skeletonize
    from skan import Skeleton, summarize

    from canmac.io.reader import get_view
    from canmac.stages.segment import preprocess_volume

    raw = np.ascontiguousarray(
        get_view(dataset, "candida", t, correct=False).compute()
    ).astype(np.float32)
    pre = preprocess_volume(raw, preprocess, tophat_radius=tophat_radius, dog_low=dog_low, dog_high=dog_high)
    thr = remove_small_objects(pre > threshold_otsu(pre), min_size)
    labels = cc_label(thr)
    skel = skeletonize(thr)
    feats = {}
    if skel.any():
        df = summarize(Skeleton(skel, spacing=(vox, vox, vox)), separator="_")
        for _sid, g in df.groupby("skeleton_id"):
            L = float(g["branch_distance"].sum())
            junc = bool((g["branch_type"] >= 1).any() and len(g) > 1)
            z, y, x = (int(g["image_coord_src_0"].iloc[0]),
                       int(g["image_coord_src_1"].iloc[0]),
                       int(g["image_coord_src_2"].iloc[0]))
            feats[int(labels[z, y, x])] = (round(L, 2), junc, (L >= len_um_hyphae or junc))
    rows = []
    class_vol = np.zeros_like(labels, dtype="uint8")
    for prop in regionprops(labels):
        L, junc, ishy = feats.get(prop.label, (0.0, False, False))
        class_vol[labels == prop.label] = 2 if ishy else 1  # 1=yeast, 2=hyphae
        rows.append({"obj": prop.label, "vox": int(prop.area), "skel_len_um": L,
                     "branched": junc, "PROPOSED_class": "hyphae" if ishy else "yeast"})
    table = pd.DataFrame(rows).sort_values("vox", ascending=False)
    return raw, thr.astype("uint8"), skel.astype("uint8"), class_vol, table


def main() -> None:
    ap = argparse.ArgumentParser(description="View the PROPOSED candida threshold/skeleton split in napari.")
    ap.add_argument("t", type=int, help="Timepoint index (e.g. 90).")
    ap.add_argument("--dataset", default="ROI2", choices=["ROI2", "ROI7"])
    ap.add_argument("--preprocess", default="dog", choices=["none", "dog", "tophat"])
    ap.add_argument("--tophat-radius", type=int, default=10, dest="tophat_radius")
    ap.add_argument("--dog-low", type=float, default=1.0, dest="dog_low")
    ap.add_argument("--dog-high", type=float, default=6.0, dest="dog_high")
    ap.add_argument("--min-size", type=int, default=8, dest="min_size")
    ap.add_argument("--len-um-hyphae", type=float, default=3.0, dest="len_um_hyphae")
    ap.add_argument("--vox", type=float, default=0.14499219272808386)
    ap.add_argument("--check", action="store_true", help="Print the table, do not open napari.")
    args = ap.parse_args()

    raw, thr, skel, class_vol, table = compute_split(
        args.dataset, args.t, args.preprocess, args.tophat_radius, args.dog_low,
        args.dog_high, args.min_size, args.len_um_hyphae, args.vox)
    print(f"{args.dataset}/candida t{args.t:03d} — PROPOSED (confirm visually, nothing written to disk):")
    print(table.to_string(index=False))

    if args.check:
        print("--check: skipping napari GUI.")
        return

    import napari

    s = (args.vox, args.vox, args.vox)
    v = napari.Viewer(ndisplay=3)
    v.add_image(raw, name=f"candida raw t{args.t:03d}", scale=s, colormap="magenta", blending="additive")
    v.add_labels(thr, name="threshold foreground", scale=s, visible=False)
    v.add_image(skel, name="skeleton", scale=s, colormap="red", blending="additive")
    v.add_labels(class_vol, name="PROPOSED class (1=yeast,2=hyphae)", scale=s)
    v.scale_bar.visible = True
    v.scale_bar.unit = "um"
    napari.run()


if __name__ == "__main__":
    main()
