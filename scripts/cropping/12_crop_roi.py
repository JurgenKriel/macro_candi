#!/usr/bin/env python
"""Crop an ROI out of full_stack/raw.zarr into a small OME-Zarr for Blender.

Takes the SAME level-0 coordinates you settled on with 11_preview_crop.py and
streams that sub-volume into a new OME-Zarr store that Microscopy Nodes can open
directly. Native resolution is preserved — the point of cropping instead of
downsampling is to stay under Blender's ~4 GiB per-volume-grid ceiling without
losing detail.

Usage (VNC GPU desktop terminal, or a compute node):
    export PATH="$HOME/.pixi/bin:$PATH"
    cd /vast/scratch/users/kriel.j/monash_lsm

    # dry run first — reports size and writes nothing
    pixi run python 12_crop_roi.py --x0 1200 --x1 2700 --y0 300 --y1 1800 \
        --out full_stack/roi_a.zarr --dry-run

    # single timepoint
    pixi run python 12_crop_roi.py --x0 1200 --x1 2700 --y0 300 --y1 1800 \
        --out full_stack/roi_a.zarr

    # a stretch of timelapse (Microscopy Nodes loads this as a VDB sequence)
    pixi run python 12_crop_roi.py --x0 1200 --x1 2700 --y0 300 --y1 1800 \
        --t0 0 --t1 40 --out full_stack/roi_a.zarr

The written store is a proper OME-Zarr 0.4 multiscale group nested under /0, the
same layout bioformats2raw produces, so it opens in napari-ome-zarr and in
Microscopy Nodes with no further conversion.

Unlike the source pyramid (which downsamples XY only, leaving z 2-4x finer than
xy at levels 1+), the extra levels written here are downsampled in Z as well, so
every level stays isotropic — which is what you want for 3D volume rendering.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]  # repo root (script lives in scripts/cropping/)
os.chdir(REPO)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

DEFAULT_STORE = os.environ.get("CANMAC_FULLSTACK_ZARR", "full_stack/raw.zarr")
BLENDER_LIMIT_GIB = 4.0
VDB_BYTES_PER_VOXEL = 4


def _import_preview():
    """Reuse the preview script's metadata/box helpers so both agree exactly."""
    import importlib.util
    path = REPO / "11_preview_crop.py"
    if not path.exists():
        sys.exit(f"ERROR: {path} not found (needed for coordinate handling)")
    spec = importlib.util.spec_from_file_location("_preview", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Crop an ROI from full_stack/raw.zarr into a Blender-ready OME-Zarr.")
    ap.add_argument("--store", default=DEFAULT_STORE, help="source raw.zarr root")
    ap.add_argument("--out", required=True, help="output .zarr path")
    for ax in "xyz":
        ap.add_argument(f"--{ax}0", type=int, default=None,
                        help=f"{ax} start in LEVEL-0 voxels (default: 0)")
        ap.add_argument(f"--{ax}1", type=int, default=None,
                        help=f"{ax} stop in LEVEL-0 voxels (default: full extent)")
    ap.add_argument("--t0", type=int, default=0, help="first timepoint (default 0)")
    ap.add_argument("--t1", type=int, default=None,
                    help="stop timepoint, exclusive (default: t0+1, i.e. one frame)")
    ap.add_argument("--channels", default="all",
                    help="'all', or comma-separated names (candida,macrophage) / indices")
    ap.add_argument("--include-lysed", action="store_true", dest="include_lysed",
                    help="also include the lysed FF00FF channel (excluded by default, D-03)")
    ap.add_argument("--level", type=int, default=0,
                    help="source pyramid level to crop FROM (default 0 = native)")
    ap.add_argument("--extra-levels", type=int, default=2,
                    help="isotropic 2x downsample levels to add (default 2)")
    ap.add_argument("--chunk-xy", type=int, default=256, help="output chunk in x/y (default 256)")
    ap.add_argument("--chunk-z", type=int, default=64, help="output chunk in z (default 64)")
    ap.add_argument("--dry-run", action="store_true", help="report size and exit, write nothing")
    ap.add_argument("--overwrite", action="store_true", help="replace --out if it exists")
    args = ap.parse_args()

    pc = _import_preview()

    if not os.path.isdir(args.store):
        sys.exit(f"ERROR: no such store: {args.store}")

    levels, axes, omero = pc.read_multiscale(args.store)
    shape0 = levels[0]["shape"]
    if not 0 <= args.level < len(levels):
        sys.exit(f"ERROR: --level {args.level} but only {len(levels)} levels exist")

    n_t = pc.axis_len(axes, shape0, "t")
    t1 = args.t0 + 1 if args.t1 is None else args.t1
    if not (0 <= args.t0 < t1 <= n_t):
        sys.exit(f"ERROR: bad timepoint range [{args.t0}:{t1}] for T={n_t}")

    chans = pc.select_channels(args.store, args.channels, omero, args.include_lysed)

    print(f"source : {args.store}")
    print(f"out    : {args.out}")
    print(f"level 0: {shape0}  axes {axes}")
    print(f"t      : [{args.t0}:{t1}]  ({t1 - args.t0} timepoint(s))")
    print(f"channel: " + ", ".join(f"{i}:{lab}({col})" for i, lab, col in chans))

    print("\nROI box (level-0 voxels):")
    box0 = pc.resolve_box(args, axes, shape0)
    um0 = {d: levels[0]["scale"][axes.index(d)] for d in "xyz"}
    for d in "xyz":
        lo, hi = box0[d]
        print(f"  {d}: [{lo}:{hi}]  {hi - lo} vox  {(hi - lo) * um0[d]:.1f} um")

    src = levels[args.level]
    b = pc.scale_box(box0, shape0, src["shape"], axes)
    dims = {d: b[d][1] - b[d][0] for d in "xyz"}
    vox = {d: src["scale"][axes.index(d)] for d in "xyz"}

    nvox = dims["z"] * dims["y"] * dims["x"]
    vdb_gib = nvox * VDB_BYTES_PER_VOXEL / 2 ** 30
    disk_gib = nvox * 2 * len(chans) * (t1 - args.t0) / 2 ** 30  # uint16 on disk

    print(f"\ncropping from level {args.level}: {dims['z']} x {dims['y']} x {dims['x']} "
          f"(z,y,x)  voxel {tuple(round(vox[d], 4) for d in 'zyx')} um")
    print(f"  as float32 VDB, per channel per timepoint : {vdb_gib:.2f} GiB "
          f"({'fits' if vdb_gib <= BLENDER_LIMIT_GIB else f'OVER Blender 4 GiB by {vdb_gib / BLENDER_LIMIT_GIB:.1f}x'})")
    print(f"  uint16 on disk, all channels/timepoints   : {disk_gib:.2f} GiB")
    if vdb_gib > BLENDER_LIMIT_GIB:
        print("  ! this ROI will NOT display in Blender's viewport/EEVEE as-is.")
        print("    Shrink the box, or render it in Cycles instead.")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    if os.path.exists(args.out):
        if not args.overwrite:
            sys.exit(f"ERROR: {args.out} exists (pass --overwrite to replace)")
        print(f"\nremoving existing {args.out}")
        shutil.rmtree(args.out)

    # ---------- write ----------
    import dask.array as da
    import zarr

    arr = da.from_zarr(args.store, component=f"0/{src['path']}")
    idx = tuple(slice(args.t0, t1) if d == "t"
                else [i for i, _, _ in chans] if d == "c"
                else slice(*b[d]) for d in axes)
    # index axes one at a time: fancy-indexing several axes at once is ambiguous
    sub = arr
    for pos, sl in enumerate(idx):
        sub = sub[(slice(None),) * pos + (sl,)]

    # zarr_format=2 throughout: the source store is v2, and v2 is what every env
    # here (and the zarr bundled with Microscopy Nodes) reads without surprises.
    root = zarr.open_group(args.out, mode="w", zarr_format=2)
    root.attrs["bioformats2raw.layout"] = 3
    grp = root.create_group("0")

    datasets = []
    cur = sub
    t0w = time.time()
    for lvl in range(args.extra_levels + 1):
        if lvl > 0:
            # isotropic 2x: halve z, y and x together (source pyramid does XY only)
            trim = tuple(slice(0, s - (s % 2)) if d in "zyx" else slice(None)
                         for d, s in zip(axes, cur.shape))
            cur = cur[trim]
            if min(cur.shape[axes.index(d)] for d in "zyx") < 2:
                print(f"  level {lvl}: too small to downsample further, stopping")
                break
            cur = da.coarsen(np.mean, cur,
                             {axes.index(d): 2 for d in "zyx"}).astype(sub.dtype)

        chunks = tuple(1 if d in "tc" else
                       args.chunk_z if d == "z" else args.chunk_xy for d in axes)
        chunks = tuple(min(c, s) for c, s in zip(chunks, cur.shape))
        out = cur.rechunk(chunks)
        print(f"  writing level {lvl}: {out.shape} chunks {chunks} ...", flush=True)
        out.to_zarr(args.out, component=f"0/{lvl}", overwrite=True, zarr_format=2)
        datasets.append({
            "path": str(lvl),
            "coordinateTransformations": [{
                "type": "scale",
                "scale": [1.0 if d in "tc" else vox[d] * (2 ** lvl) for d in axes],
            }],
        })

    grp.attrs["multiscales"] = [{
        "version": "0.4",
        "name": os.path.basename(args.out),
        "axes": [{"name": d, "type": {"t": "time", "c": "channel"}.get(d, "space"),
                  **({} if d in "tc" else {"unit": "micrometer"})} for d in axes],
        "datasets": datasets,
        "metadata": {"method": "12_crop_roi.py", "note": "isotropic 3D downsample"},
    }]
    grp.attrs["omero"] = {
        "channels": [{
            "label": lab, "color": col, "active": True, "coefficient": 1,
            "family": "linear", "inverted": False,
            "window": {"min": 0.0, "max": 65535.0, "start": 0.0, "end": 6500.0},
        } for _, lab, col in chans],
        "rdefs": {"defaultT": 0, "defaultZ": dims["z"] // 2, "model": "color"},
    }

    print(f"\ndone in {time.time() - t0w:.1f}s -> {args.out}")
    print("\nOpen in Blender: Microscopy Nodes -> file path")
    print(f"  {os.path.abspath(args.out)}/0")
    print("Check in napari first if you like:")
    print(f"  pixi run napari --plugin napari-ome-zarr {os.path.abspath(args.out)}/0")


if __name__ == "__main__":
    main()
