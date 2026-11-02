#!/usr/bin/env python
"""Crop an ROI out of full_stack/raw.zarr into a small OME-Zarr for Blender.

Two ways to use it:

  GUI (recommended): draw the box in napari, set Z / time / channels in the Crop panel,
  press Crop. Needs a desktop session with a display.
      pixi run python scripts/cropping/12_crop_roi.py --gui
      pixi run python scripts/cropping/12_crop_roi.py --gui --store data/demo/DEMO/raw.zarr

  Command line (batch jobs, SSH, scripts): give the level-0 box directly. The GUI's
  "Copy command" button produces exactly this line.
      C=scripts/cropping/12_crop_roi.py
      pixi run python $C --x0 1200 --x1 2700 --y0 300 --y1 1800 --out data/roi_a.zarr --dry-run
      pixi run python $C --x0 1200 --x1 2700 --y0 300 --y1 1800 --out data/roi_a.zarr
      pixi run python $C --x0 1200 --x1 2700 --y0 300 --y1 1800 --t0 0 --t1 40 --out data/roi_a.zarr

Native resolution is preserved: the point of cropping instead of downsampling is to stay
under Blender's ~4 GiB per-volume-grid ceiling without losing detail.

Relative --store / --out paths are resolved against the directory you run the command from.
Guide: https://jurgenkriel.github.io/macro_candi/pipeline/04-cropping/

The written store is a proper OME-Zarr 0.4 multiscale group nested under /0, the
same layout bioformats2raw produces, so Microscopy Nodes opens it with no further
conversion. Unlike the source pyramid (which downsamples XY only), the extra levels
written here are downsampled in Z as well, so every level stays isotropic. The source
box and time range are recorded in the multiscales metadata ("canmac_crop").
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]  # repo root (script lives in scripts/cropping/)
CALLER_CWD = pathlib.Path.cwd()  # captured BEFORE chdir so user-given relative paths mean what they say
os.chdir(REPO)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

DEFAULT_STORE = os.environ.get("CANMAC_FULLSTACK_ZARR", "full_stack/raw.zarr")
BLENDER_LIMIT_GIB = 4.0
VDB_BYTES_PER_VOXEL = 4

_PC = None


def _import_preview():
    """Reuse the preview script's metadata/box helpers so both agree exactly."""
    global _PC
    if _PC is None:
        import importlib.util
        path = pathlib.Path(__file__).resolve().with_name("11_preview_crop.py")
        if not path.exists():
            sys.exit(f"ERROR: {path} not found (needed for coordinate handling)")
        spec = importlib.util.spec_from_file_location("_preview", path)
        mod = importlib.util.module_from_spec(spec)
        cwd = os.getcwd()
        spec.loader.exec_module(mod)
        os.chdir(cwd)
        _PC = mod
    return _PC


def estimate(levels, axes: str, box0: dict, level: int, n_chan: int, n_t: int) -> dict:
    """Sizes for cropping ``box0`` (level-0 voxels) from ``level``.

    Returns dims/voxel size at that level, the float32 VDB size Blender will build per
    channel per timepoint, whether that fits under the ~4 GiB limit, and uint16 disk size.
    """
    pc = _import_preview()
    src = levels[level]
    b = pc.scale_box(box0, levels[0]["shape"], src["shape"], axes)
    dims = {d: b[d][1] - b[d][0] for d in "xyz"}
    nvox = dims["z"] * dims["y"] * dims["x"]
    vdb_gib = nvox * VDB_BYTES_PER_VOXEL / 2 ** 30
    return {
        "box": b,
        "dims": dims,
        "vox": {d: src["scale"][axes.index(d)] for d in "xyz"},
        "vdb_gib": vdb_gib,
        "fits": vdb_gib <= BLENDER_LIMIT_GIB,
        "disk_gib": nvox * 2 * n_chan * n_t / 2 ** 30,
    }


def crop_command(store: str, out: str, box0: dict, t0: int, t1: int, channels: str,
                 extra_levels: int = 2, include_lysed: bool = False) -> str:
    """The equivalent command line, for batch jobs / reproducibility."""
    pc = _import_preview()
    parts = ["pixi run python scripts/cropping/12_crop_roi.py", f"--store {store}",
             pc.box_flags(box0), f"--t0 {t0} --t1 {t1}", f"--channels {channels}"]
    if include_lysed:
        parts.append("--include-lysed")
    if extra_levels != 2:
        parts.append(f"--extra-levels {extra_levels}")
    parts.append(f"--out {out}")
    return " ".join(parts)


class _DaskProgress:
    """Report a dask computation's finished-task fraction to ``fn(frac)``."""

    def __init__(self, fn, base: float, span: float):
        from dask.callbacks import Callback

        outer = self

        class _CB(Callback):
            def _posttask(self, key, result, dsk, state, worker_id):
                done = len(state["finished"])
                left = len(state["ready"]) + len(state["running"]) + len(state["waiting"])
                outer.fn(outer.base + outer.span * done / max(1, done + left))

        self.fn, self.base, self.span, self.cb = fn, base, span, _CB()

    def __enter__(self):
        return self.cb.__enter__()

    def __exit__(self, *exc):
        return self.cb.__exit__(*exc)


def write_crop(store: str, out: str, levels, axes: str, box0: dict, t0: int, t1: int,
               chans: list, level: int = 0, extra_levels: int = 2, chunk_xy: int = 256,
               chunk_z: int = 64, overwrite: bool = False, progress=None, log=print) -> str:
    """Stream ``box0`` (level-0 voxels) x [t0:t1) x ``chans`` from ``store`` into ``out``.

    ``chans`` is [(index, label, hexcolor)] as from select_channels. ``progress(frac)`` is
    called with 0..1 as data is written (from the calling thread). Returns ``out``.
    Raises FileExistsError if ``out`` exists and ``overwrite`` is False.
    """
    import dask.array as da
    import zarr

    pc = _import_preview()
    if os.path.exists(out):
        if not overwrite:
            raise FileExistsError(f"{out} exists (pass --overwrite to replace)")
        log(f"removing existing {out}")
        shutil.rmtree(out)

    src = levels[level]
    b = pc.scale_box(box0, levels[0]["shape"], src["shape"], axes)
    vox = {d: src["scale"][axes.index(d)] for d in "xyz"}
    arr = da.from_zarr(store, component=f"0/{src['path']}")
    idx = tuple(slice(t0, t1) if d == "t"
                else [i for i, _, _ in chans] if d == "c"
                else slice(*b[d]) for d in axes)
    # index axes one at a time: fancy-indexing several axes at once is ambiguous
    sub = arr
    for pos, sl in enumerate(idx):
        sub = sub[(slice(None),) * pos + (sl,)]

    # zarr_format=2 throughout: the source store is v2, and v2 is what every env
    # here (and the zarr bundled with Microscopy Nodes) reads without surprises.
    root = zarr.open_group(out, mode="w", zarr_format=2)
    root.attrs["bioformats2raw.layout"] = 3
    grp = root.create_group("0")

    # progress weights: level L holds ~1/8^L of level 0's voxels
    weights = [8.0 ** -lvl for lvl in range(extra_levels + 1)]
    wsum, done_w = sum(weights), 0.0
    report = progress or (lambda f: None)
    report(0.0)

    datasets = []
    cur = sub
    for lvl in range(extra_levels + 1):
        if lvl > 0:
            # isotropic 2x from the level just written: halve z, y and x together
            prev = da.from_zarr(out, component=f"0/{lvl - 1}")
            trim = tuple(slice(0, s - (s % 2)) if d in "zyx" else slice(None)
                         for d, s in zip(axes, prev.shape))
            prev = prev[trim]
            if min(prev.shape[axes.index(d)] for d in "zyx") < 2:
                log(f"  level {lvl}: too small to downsample further, stopping")
                break
            cur = da.coarsen(np.mean, prev,
                             {axes.index(d): 2 for d in "zyx"}).astype(sub.dtype)

        chunks = tuple(1 if d in "tc" else chunk_z if d == "z" else chunk_xy for d in axes)
        chunks = tuple(min(c, s) for c, s in zip(chunks, cur.shape))
        o = cur.rechunk(chunks)
        log(f"  writing level {lvl}: {o.shape} chunks {chunks} ...")
        span = weights[lvl] / wsum
        with _DaskProgress(report, done_w, span):
            o.to_zarr(out, component=f"0/{lvl}", overwrite=True, zarr_format=2)
        done_w += span
        datasets.append({
            "path": str(lvl),
            "coordinateTransformations": [{
                "type": "scale",
                "scale": [1.0 if d in "tc" else vox[d] * (2 ** lvl) for d in axes],
            }],
        })

    grp.attrs["multiscales"] = [{
        "version": "0.4",
        "name": os.path.basename(out.rstrip("/")),
        "axes": [{"name": d, "type": {"t": "time", "c": "channel"}.get(d, "space"),
                  **({} if d in "tc" else {"unit": "micrometer"})} for d in axes],
        "datasets": datasets,
        "metadata": {
            "method": "12_crop_roi.py", "note": "isotropic 3D downsample",
            "canmac_crop": {"source": os.path.abspath(store), "source_level": level,
                            "box_level0": {d: list(box0[d]) for d in "zyx"},
                            "t": [t0, t1], "channels": [i for i, _, _ in chans]},
        },
    }]
    dz = b["z"][1] - b["z"][0]
    grp.attrs["omero"] = {
        "channels": [{
            "label": lab, "color": col, "active": True, "coefficient": 1,
            "family": "linear", "inverted": False,
            "window": {"min": 0.0, "max": 65535.0, "start": 0.0, "end": 6500.0},
        } for _, lab, col in chans],
        "rdefs": {"defaultT": 0, "defaultZ": dz // 2, "model": "color"},
    }
    report(1.0)
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description="Crop an ROI from full_stack/raw.zarr into a Blender-ready OME-Zarr. "
                    "Use --gui to draw the box in napari instead of typing coordinates.")
    ap.add_argument("--gui", action="store_true",
                    help="open the napari crop GUI (draw the box, press Crop). Box/--t0/"
                         "--channels flags pre-fill it; --out is then optional")
    ap.add_argument("--store", default=None,
                    help="source raw.zarr root (default: $CANMAC_FULLSTACK_ZARR, else full_stack/raw.zarr)")
    ap.add_argument("--out", default=None, help="output .zarr path (required without --gui)")
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
    args = ap.parse_args(argv)

    pc = _import_preview()
    args.store = pc.user_path(args.store, DEFAULT_STORE) if args.store is None else \
        str((CALLER_CWD / os.path.expanduser(args.store)).resolve())

    if args.gui:
        fwd = [str(args.t0), "--store", args.store, "--channels", args.channels]
        if args.include_lysed:
            fwd.append("--include-lysed")
        for d in "xyz":
            for e in "01":
                val = getattr(args, f"{d}{e}")
                if val is not None:
                    fwd += [f"--{d}{e}", str(val)]
        return pc.main(fwd)

    if args.out is None:
        ap.error("--out is required (or use --gui to pick the box and output in napari)")
    args.out = str((CALLER_CWD / os.path.expanduser(args.out)).resolve())

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

    est = estimate(levels, axes, box0, args.level, len(chans), t1 - args.t0)
    dims, vox, vdb_gib = est["dims"], est["vox"], est["vdb_gib"]
    print(f"\ncropping from level {args.level}: {dims['z']} x {dims['y']} x {dims['x']} "
          f"(z,y,x)  voxel {tuple(round(vox[d], 4) for d in 'zyx')} um")
    print(f"  as float32 VDB, per channel per timepoint : {vdb_gib:.2f} GiB "
          f"({'fits' if est['fits'] else f'OVER Blender 4 GiB by {vdb_gib / BLENDER_LIMIT_GIB:.1f}x'})")
    print(f"  uint16 on disk, all channels/timepoints   : {est['disk_gib']:.2f} GiB")
    if not est["fits"]:
        print("  ! this ROI will NOT display in Blender's viewport/EEVEE as-is.")
        print("    Shrink the box, or render it in Cycles instead.")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    t_start = time.time()
    last = [-1]

    def bar(frac):
        pct = int(frac * 100)
        if pct // 10 != last[0] // 10:  # a line every 10 %
            last[0] = pct
            print(f"  {pct:3d} %", flush=True)

    print()
    try:
        write_crop(args.store, args.out, levels, axes, box0, args.t0, t1, chans,
                   level=args.level, extra_levels=args.extra_levels, chunk_xy=args.chunk_xy,
                   chunk_z=args.chunk_z, overwrite=args.overwrite, progress=bar,
                   log=lambda m: print(m, flush=True))
    except FileExistsError as e:
        sys.exit(f"ERROR: {e}")

    print(f"\ndone in {time.time() - t_start:.1f}s -> {args.out}")
    print("\nOpen in Blender: Microscopy Nodes -> file path")
    print(f"  {os.path.abspath(args.out)}/0")
    print("Check it in napari first (same viewer, pointed at the crop; coordinates restart at 0):")
    print(f"  pixi run python scripts/cropping/11_preview_crop.py 0 --store {os.path.abspath(args.out)} --level 0")


if __name__ == "__main__":
    main()
