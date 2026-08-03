#!/usr/bin/env python
"""Preview a candidate ROI of the full_stack OME-Zarr in napari (read-only).

Opens ONE timepoint of a coordinate-defined sub-volume as lazy dask layers so you
can confirm a crop box in 3D before anything is written. Writes NOTHING to disk —
no zarr, no tiff, no cache. Run this, agree on a box, then hand the same numbers
to the cropper.

Usage (VNC GPU desktop terminal):
    export PATH="$HOME/.pixi/bin:$PATH"
    cd /vast/scratch/users/kriel.j/monash_lsm
    pixi run python 11_preview_crop.py 0                                  # whole volume, level 2
    pixi run python 11_preview_crop.py 0 --x0 1200 --x1 2700 --y0 300 --y1 1800
    pixi run python 11_preview_crop.py 0 --x0 1200 --x1 2700 --level 0    # native res

COORDINATES ARE ALWAYS LEVEL-0 (NATIVE) VOXELS, whatever --level you preview at.
The box is rescaled onto the previewed level for you, so the numbers you settle on
here mean the same thing to the cropper regardless of the level you eyeballed them
at. Omitted bounds default to the full extent of that axis.

Why this matters: Microscopy Nodes builds float32 VDBs and Blender's EEVEE/viewport
tops out around 4 GiB per volume grid (blender/blender#136263). The full level-0
stack is 11.6 GiB per channel, so it cannot be displayed whole. Cropping keeps
native resolution and gets under the ceiling; the size table printed below tells
you whether the box you picked actually fits.

--check   print the size table only, no napari GUI (headless / over ssh).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]  # repo root (script lives in scripts/cropping/)
os.chdir(REPO)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

# Default store: the 490 GB full CZI conversion (02_convert_fullstack.sh).
DEFAULT_STORE = os.environ.get("CANMAC_FULLSTACK_ZARR", "full_stack/raw.zarr")

# Blender/EEVEE ceiling per volume grid; Microscopy Nodes writes float32 VDBs.
BLENDER_LIMIT_GIB = 4.0
VDB_BYTES_PER_VOXEL = 4


def _zattrs_path(store: str) -> str:
    """bioformats2raw nests the OME-Zarr multiscale group under <store>/0."""
    return os.path.join(store, "0", ".zattrs")


def read_multiscale(store: str):
    """Return (levels, axes_order, omero_channels) from the multiscale .zattrs.

    Read straight off disk rather than through canmac.io.manifest: that manifest
    validates to EXACTLY {ROI2, ROI7} with T==120 (D-01/D-02), and full_stack is
    neither. Channel naming still goes through canmac.io.channels so the lysed
    channel policy (D-03) is honoured in one place.
    """
    with open(_zattrs_path(store)) as f:
        attrs = json.load(f)
    ms = attrs["multiscales"][0]
    axes = "".join(a["name"] if isinstance(a, dict) else a for a in ms["axes"])

    levels = []
    for ds in ms["datasets"]:
        with open(os.path.join(store, "0", ds["path"], ".zarray")) as f:
            shape = tuple(json.load(f)["shape"])
        scale = next((t["scale"] for t in ds.get("coordinateTransformations", [])
                      if t["type"] == "scale"), [1.0] * len(axes))
        levels.append({"path": ds["path"], "shape": shape, "scale": list(scale)})
    return levels, axes, attrs.get("omero", {}).get("channels", [])


def axis_len(axes: str, shape, dim: str) -> int:
    return shape[axes.index(dim)] if dim in axes else 1


def resolve_box(args, axes: str, shape0) -> dict:
    """Clamp the requested level-0 box to the volume. Fails loud on an empty range."""
    box = {}
    for dim in "xyz":
        n = axis_len(axes, shape0, dim)
        lo = getattr(args, f"{dim}0")
        hi = getattr(args, f"{dim}1")
        lo = 0 if lo is None else lo
        hi = n if hi is None else hi
        clo, chi = max(0, lo), min(n, hi)
        if (clo, chi) != (lo, hi):
            print(f"  ! {dim}: [{lo}:{hi}] clamped to [{clo}:{chi}] (extent {n})")
        if clo >= chi:
            sys.exit(f"ERROR: empty range on {dim}: [{clo}:{chi}]")
        box[dim] = (clo, chi)
    return box


def scale_box(box: dict, shape0, shape_l, axes: str) -> dict:
    """Map a level-0 box onto another pyramid level by that level's own ratio."""
    out = {}
    for dim in "xyz":
        r = axis_len(axes, shape_l, dim) / axis_len(axes, shape0, dim)
        lo, hi = box[dim]
        slo = int(np.floor(lo * r))
        shi = max(slo + 1, int(np.ceil(hi * r)))
        out[dim] = (slo, min(shi, axis_len(axes, shape_l, dim)))
    return out


def select_channels(store: str, spec: str, omero, include_lysed: bool) -> list:
    """Return [(index, label, hexcolor)] for the requested channels.

    Named channels resolve by omero COLOR through canmac.io.channels (never by
    index), which is what keeps the lysed channel out of a read (D-03).
    """
    from canmac.io.channels import discard_indices, resolve_channel

    zattrs = _zattrs_path(store)
    lysed = set(discard_indices(zattrs))

    if spec == "all":
        idxs = [i for i in range(len(omero)) if include_lysed or i not in lysed]
    else:
        idxs = []
        for tok in (t.strip() for t in spec.split(",") if t.strip()):
            if tok.isdigit():
                i = int(tok)
                if not 0 <= i < len(omero):
                    sys.exit(f"ERROR: channel index {i} out of range 0..{len(omero) - 1}")
            else:
                i = resolve_channel(zattrs, tok)  # by color; raises on lysed/unknown
            idxs.append(i)
        blocked = [i for i in idxs if i in lysed and not include_lysed]
        if blocked:
            sys.exit(f"ERROR: channel(s) {blocked} are the lysed channel (D-03). "
                     f"Pass --include-lysed if you really want it in a preview.")
    if not idxs:
        sys.exit("ERROR: no channels selected")
    return [(i, omero[i].get("label") or f"c{i}", omero[i].get("color")) for i in idxs]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Preview a coordinate-defined ROI of the full_stack in napari (writes nothing).")
    ap.add_argument("t", type=int, help="Timepoint index (e.g. 0).")
    ap.add_argument("--store", default=DEFAULT_STORE, help="raw.zarr root (default: full_stack)")
    ap.add_argument("--level", type=int, default=2,
                    help="pyramid level to PREVIEW at; 0=native (default: 2, fast)")
    ap.add_argument("--channels", default="all",
                    help="'all', or comma-separated names (candida,macrophage) / indices")
    ap.add_argument("--include-lysed", action="store_true", dest="include_lysed",
                    help="also show the lysed FF00FF channel (excluded by default, D-03)")
    for ax in "xyz":
        ap.add_argument(f"--{ax}0", type=int, default=None,
                        help=f"{ax} start in LEVEL-0 voxels (default: 0)")
        ap.add_argument(f"--{ax}1", type=int, default=None,
                        help=f"{ax} stop in LEVEL-0 voxels (default: full extent)")
    ap.add_argument("--check", action="store_true",
                    help="print the size table only, do not open napari.")
    args = ap.parse_args()

    if not os.path.isdir(args.store):
        sys.exit(f"ERROR: no such store: {args.store}")

    levels, axes, omero = read_multiscale(args.store)
    shape0 = levels[0]["shape"]
    if not 0 <= args.level < len(levels):
        sys.exit(f"ERROR: --level {args.level} but only {len(levels)} levels exist")

    n_t = axis_len(axes, shape0, "t")
    if not 0 <= args.t < n_t:
        sys.exit(f"ERROR: timepoint {args.t} out of range 0..{n_t - 1}")

    chans = select_channels(args.store, args.channels, omero, args.include_lysed)

    print(f"store  : {args.store}")
    print(f"axes   : {axes}   levels: {len(levels)}   level 0: {shape0}")
    print(f"t      : {args.t} of {n_t}")
    print(f"channel: " + ", ".join(f"{i}:{lab}({col})" for i, lab, col in chans))

    print("\nROI box (level-0 voxels):")
    box0 = resolve_box(args, axes, shape0)
    um = {d: levels[0]["scale"][axes.index(d)] for d in "xyz"}
    for d in "xyz":
        lo, hi = box0[d]
        print(f"  {d}: [{lo}:{hi}]  {hi - lo} vox  {(hi - lo) * um[d]:.1f} um")

    print("\nthis ROI as a float32 VDB (per channel, per timepoint):")
    print(f"  {'level':>5}  {'z':>5} {'y':>5} {'x':>6}  {'GiB':>7}  vs Blender {BLENDER_LIMIT_GIB:g} GiB")
    for i, lv in enumerate(levels):
        b = scale_box(box0, shape0, lv["shape"], axes)
        d = {k: b[k][1] - b[k][0] for k in "xyz"}
        gib = d["z"] * d["y"] * d["x"] * VDB_BYTES_PER_VOXEL / 2 ** 30
        verdict = "fits" if gib <= BLENDER_LIMIT_GIB else f"OVER by {gib / BLENDER_LIMIT_GIB:.1f}x"
        mark = "  <-- previewing" if i == args.level else ""
        print(f"  {i:>5}  {d['z']:>5} {d['y']:>5} {d['x']:>6}  {gib:>7.2f}  {verdict:<14}{mark}")
    print(f"\n  {len(chans)} channel(s) -> {len(chans)} separate volume objects in Microscopy Nodes")

    if args.check:
        print("\n--check: skipping napari GUI.")
        return

    # ---- lazy crop: nothing is read until napari asks for a plane ----
    import dask.array as da

    lv = levels[args.level]
    b = scale_box(box0, shape0, lv["shape"], axes)
    arr = da.from_zarr(args.store, component=f"0/{lv['path']}")  # lazy
    idx = tuple(args.t if d == "t" else slice(None) if d == "c" else slice(*b[d])
                for d in axes)
    sub = arr[idx]  # -> (C, Z, Y, X), t dropped

    spatial = [d for d in axes if d in "zyx"]
    nap_scale = [lv["scale"][axes.index(d)] for d in spatial]
    print(f"\npreviewing level {args.level}: {sub.shape} (lazy)  "
          f"voxel {tuple(round(s, 4) for s in nap_scale)} um")

    import napari
    from napari.utils.colormaps import Colormap

    v = napari.Viewer(ndisplay=3, title=f"ROI preview  L{args.level}  t{args.t}")
    for i, label, color in chans:
        kw = {}
        if color:
            try:
                rgb = [int(color[k:k + 2], 16) / 255 for k in (0, 2, 4)]
                kw["colormap"] = Colormap([[0, 0, 0], rgb], name=f"c{i}")
            except (ValueError, IndexError):
                pass
        v.add_image(sub[i], name=f"{label} t{args.t:03d}", scale=nap_scale,
                    blending="additive", rendering="mip", **kw)
    v.scale_bar.visible = True
    v.scale_bar.unit = "um"

    print("\nHappy with the box? Note the level-0 --x0/--x1/--y0/--y1/--z0/--z1 above,")
    print("then hand those same numbers to the cropper.")
    napari.run()


if __name__ == "__main__":
    main()
