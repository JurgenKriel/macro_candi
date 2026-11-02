#!/usr/bin/env python
"""Preview the full_stack OME-Zarr in napari and crop a box from it (the crop GUI).

Opens ONE timepoint at ONE pyramid level as lazy dask layers (only the planes on screen
are read), in 2D. Draw the crop box on the "crop box" layer, set its Z range, time
range, channels and output in the Crop panel on the right, and press Crop: the box is
written at full resolution by 12_crop_roi.write_crop on a background thread. Nothing is
written until you press Crop. ``12_crop_roi.py --gui`` launches exactly this.

Usage (from a desktop session with a display: VNC / OpenOnDemand / Strudel):
    S=scripts/cropping/11_preview_crop.py
    pixi run python $S 0                                       # whole volume, coarse level
    pixi run python $S 0 --store data/demo/DEMO/raw.zarr       # practise on the demo store
    pixi run python $S 0 --x0 1200 --x1 2700 --y0 300 --y1 1800 --level 0   # finer look at a region
    pixi run python $S 0 --3d                                  # start in the 3D view
    pixi run python $S 0 --no-panel                            # view only
    pixi run python $S 0 --check                               # size table only, no GUI

Guide: https://jurgenkriel.github.io/macro_candi/pipeline/04-cropping/

COORDINATES ARE ALWAYS LEVEL-0 (NATIVE) VOXELS, whatever --level you preview at: layers
are scaled and translated so napari world coordinates are micrometres in the whole
volume, and every box (drawn, typed, J / K, flags) is converted to level-0 voxels. The
overlay in the top-left shows the cursor in level-0 voxels (napari's own status bar
shows pixel indices of the previewed layer, which are NOT the numbers the cropper wants).
J / K over opposite corners also set the box (not 1 / 2: napari binds those to the image
layer's pan-zoom / transform modes).

Opens in 2D because 2D needs no 3D textures: napari's 3D volume view segfaults on remote
desktops without GPU-routed OpenGL. Use --3d / the 2D/3D button only where that works
(vglrun, or LIBGL_ALWAYS_SOFTWARE=1).

Why crop: Microscopy Nodes builds float32 VDBs and Blender's EEVEE/viewport tops out
around 4 GiB per volume grid (blender/blender#136263). The full level-0 stack is
11.6 GiB per channel. Cropping keeps native resolution and gets under the ceiling; the
Crop panel (and --check) tells you whether the box fits.

Relative --store paths are resolved against the directory you run the command from.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]  # repo root (script lives in scripts/cropping/)
CALLER_CWD = pathlib.Path.cwd()  # captured BEFORE chdir so user-given relative paths mean what they say
os.chdir(REPO)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

# Default store (relative paths resolve against the repo root): the full CZI conversion.
DEFAULT_STORE = os.environ.get("CANMAC_FULLSTACK_ZARR", "full_stack/raw.zarr")


def user_path(p, default=None) -> str:
    """A path the user typed resolves against THEIR cwd; the default against the repo root."""
    if p is None:
        return str(REPO / default) if default is not None else None
    return str((CALLER_CWD / os.path.expanduser(p)).resolve())

# Blender/EEVEE ceiling per volume grid; Microscopy Nodes writes float32 VDBs.
BLENDER_LIMIT_GIB = 4.0
VDB_BYTES_PER_VOXEL = 4


def _zattrs_path(store: str) -> str:
    """bioformats2raw nests the OME-Zarr multiscale group under <store>/0."""
    return os.path.join(store, "0", ".zattrs")


def read_multiscale(store: str):
    """Return (levels, axes_order, omero_channels) from the multiscale .zattrs.

    Read straight off disk rather than through canmac.io.manifest, so ANY
    bioformats2raw-layout store can be previewed without a manifest entry (the full
    acquisition, a crop written by 12_crop_roi.py, the synthetic demo). Channel naming
    still goes through canmac.io.channels so the lysed channel policy (D-03) is
    honoured in one place.
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


def corners_to_box(a: dict, b: dict, extent: dict) -> dict:
    """Two picked level-0 corners -> {dim: (lo, hi)} with hi EXCLUSIVE, clamped to the volume."""
    out = {}
    for d in "zyx":
        lo, hi = sorted((a[d], b[d]))
        out[d] = (max(0, lo), min(extent[d], hi + 1))
    return out


def box_flags(box: dict) -> str:
    """The cropper/preview flags for a level-0 box, in x, y, z order."""
    return " ".join(f"--{d}0 {box[d][0]} --{d}1 {box[d][1]}" for d in "xyz")


def box_to_rect(box: dict, um: dict) -> list:
    """Level-0 box -> napari rectangle [[y, x], [y, x]] in world um.

    Voxel i covers [i - 0.5, i + 0.5) * um in world space, so the edges sit between voxels.
    """
    return [[(box["y"][0] - 0.5) * um["y"], (box["x"][0] - 0.5) * um["x"]],
            [(box["y"][1] - 0.5) * um["y"], (box["x"][1] - 0.5) * um["x"]]]


def shape_to_yx(points_yx, um: dict, extent: dict) -> dict:
    """Any drawn shape (vertices in world um, last two columns y, x) -> its level-0
    bounding box {"y": (lo, hi), "x": (lo, hi)}, hi exclusive, clamped, >= 1 voxel.
    Inverse of box_to_rect for rectangles."""
    pts = np.asarray(points_yx, dtype=float)[:, -2:]
    out = {}
    eps = 1e-6  # world -> voxel float noise must not push an exact edge into the next voxel
    for k, d in enumerate("yx"):
        # voxel i spans [i - 0.5, i + 0.5) * um: lo = voxel holding the min, hi = one past
        # the voxel holding the max (an edge exactly on a voxel boundary stays outside)
        lo = int(np.floor(pts[:, k].min() / um[d] + 0.5 + eps))
        hi = int(np.ceil(pts[:, k].max() / um[d] + 0.5 - eps))
        lo = min(max(0, lo), extent[d] - 1)
        hi = min(max(lo + 1, hi), extent[d])
        out[d] = (lo, hi)
    return out


class BoxController:
    """The one crop box (level-0 voxels) shared by the drawn shape, the J / K keys and the
    Crop panel. ``set_box`` updates every view except the one the change came from."""

    def __init__(self, viewer, layer, um: dict, extent: dict):
        self.v, self.layer, self.um, self.extent = viewer, layer, um, extent
        self.box = None
        self.listeners = []  # fn(box, source)
        self._guard = False

    def current_z(self) -> int:
        return int(np.clip(round(self.v.dims.point[0] / self.um["z"]), 0, self.extent["z"] - 1))

    def set_box(self, box, source: str = "code"):
        if box is not None:
            box = {d: (int(box[d][0]), int(box[d][1])) for d in "zyx"}
        self.box = box
        if source != "shape":
            self._guard = True
            try:
                self.layer.data = []
                if box is not None:
                    self.layer.add_rectangles([box_to_rect(box, self.um)])
            finally:
                self._guard = False
        self.recolor()
        for fn in list(self.listeners):
            fn(box, source)

    def recolor(self, *_):
        """Yellow on slices inside the box's Z range, grey outside: the 3D extent at a glance."""
        if self.box is None or not len(self.layer.data):
            return
        z0, z1 = self.box["z"]
        inside = z0 <= self.current_z() < z1
        self.layer.edge_color = "yellow" if inside else "gray"
        self.layer.opacity = 1.0 if inside else 0.6

    def on_shape_event(self, event=None):
        """User drew / edited / deleted a shape: take the last one's bounding box."""
        if self._guard:
            return
        action = str(getattr(event, "action", "changed")).lower()
        if "ing" in action:  # 'adding' / 'changing' / 'removing': wait for the end
            return
        if not len(self.layer.data):
            self.box = None
            for fn in list(self.listeners):
                fn(None, "shape")
            return
        yx = shape_to_yx(self.layer.data[-1], self.um, self.extent)
        z = self.box["z"] if self.box else (0, self.extent["z"])
        box = {"z": z, **yx}
        # normalise to ONE axis-aligned rectangle (crops are boxes), after napari finishes
        from qtpy.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.set_box(box, "shape-normalised"))
        self.box = box
        for fn in list(self.listeners):
            fn(box, "shape")


def open_viewer(sub, chans, nap_scale, nap_translate, um: dict, extent: dict,
                title: str = "ROI preview", show: bool = True, ndisplay: int = 2,
                crop_ctx: dict | None = None):
    """Build the napari viewer: one layer per channel, a live level-0 cursor readout, an
    editable "crop box" shapes layer, J / K corner picking and (with ``crop_ctx``) the Crop
    panel that writes the crop. Draw, J / K and the panel all edit one box.

    Layers carry ``scale`` (um/voxel at the previewed level) and ``translate`` (the box's
    offset in um), so napari WORLD coordinates are absolute micrometres in the whole
    volume and ``world_um / um[d]`` is the level-0 voxel index whatever box or level is
    on screen. Returned unshown when ``show=False`` (tests).

    Opens in 2D by default: boxes are drawn in 2D, and 2D needs no 3D textures, so it
    also works on remote desktops whose OpenGL segfaults on volume rendering.
    """
    import napari
    from napari.utils.colormaps import Colormap

    v = napari.Viewer(ndisplay=ndisplay, title=title, show=show)
    for i, label, color in chans:
        kw = {}
        if color:
            try:
                rgb = [int(color[k:k + 2], 16) / 255 for k in (0, 2, 4)]
                kw["colormap"] = Colormap([[0, 0, 0], rgb], name=f"c{i}")
            except (ValueError, IndexError):
                pass
        v.add_image(sub[i], name=f"{label}", scale=nap_scale,
                    translate=nap_translate, blending="additive", rendering="mip",
                    units=("um",) * len(nap_scale), **kw)
    v.scale_bar.visible = True  # reads um from the layers' units (scale_bar.unit is a no-op)
    v.dims.axis_labels = ("z", "y", "x")
    v.axes.visible = True
    v.axes.labels = True

    # a 2D shapes layer is broadcast over Z: the rectangle shows on every slice
    layer = v.add_shapes(None, ndim=2, name="crop box", edge_color="yellow",
                         face_color="transparent", edge_width=2 * um["x"],
                         units=("um", "um"))
    layer.current_edge_color = "yellow"
    layer.current_face_color = "transparent"
    layer.current_edge_width = 2 * um["x"]
    ctrl = BoxController(v, layer, um, extent)
    layer.events.data.connect(ctrl.on_shape_event)
    v.dims.events.point.connect(ctrl.recolor)  # `point` is a real field in napari >= 0.8
    v.layers.selection.active = v.layers[0]  # image active: J / K and panning work at once

    picked: dict = {}

    def cursor_vox0() -> dict:
        pos = list(v.cursor.position)[-3:]  # world um, (z, y, x)
        return {d: int(np.clip(round(pos[k] / um[d]), 0, extent[d] - 1))
                for k, d in enumerate("zyx")}

    def refresh(*_):
        v.axes.visible = v.dims.ndisplay == 3  # in 2D the axes sit on top of this overlay
        c = cursor_vox0()
        lines = [f"cursor (level-0 voxels)   x {c['x']}   y {c['y']}   z {c['z']}"]
        if v.dims.ndisplay == 3:
            lines.append("3D view: positions are approximate - switch to 2D to draw the box")
        for key in ("1", "2"):
            if key in picked:
                p = picked[key]
                lines.append(f"corner {key}:  x {p['x']}  y {p['y']}  z {p['z']}")
        if ctrl.box is not None:
            z0, z1 = ctrl.box["z"]
            where = "inside" if z0 <= ctrl.current_z() < z1 else "OUTSIDE"
            lines.append(f"box: {box_flags(ctrl.box)}   (this slice is {where} z {z0}-{z1 - 1})")
        else:
            lines.append("draw a rectangle on 'crop box', or press J / K on opposite corners")
        v.text_overlay.text = "\n".join(lines)

    def pick(key: str):
        picked[key] = cursor_vox0()
        p = picked[key]
        print(f"corner {key}: x {p['x']}  y {p['y']}  z {p['z']}   (level-0 voxels)")
        if len(picked) == 2:
            box = corners_to_box(picked["1"], picked["2"], extent)
            print(f"\ncrop box flags:\n  {box_flags(box)}\n")
            ctrl.set_box(box, "keys")
        refresh()

    ctrl.listeners.append(lambda box, source: refresh())
    v.dims.events.point.connect(refresh)
    # J / K, not 1 / 2: the active layer's keymap wins over the viewer's, and napari
    # binds 1 / 2 on image layers to pan-zoom / transform mode.
    v.bind_key("j", lambda _v: pick("1"), overwrite=True)
    v.bind_key("k", lambda _v: pick("2"), overwrite=True)
    v.mouse_move_callbacks.append(refresh)
    v.dims.events.ndisplay.connect(refresh)
    v.text_overlay.visible = True
    v.text_overlay.position = "top_left"
    v.text_overlay.font_size = 10
    refresh()
    v._canmac_pick = pick  # exposed for tests
    v._canmac_box = ctrl

    if crop_ctx is not None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_crop_panel", pathlib.Path(__file__).resolve().with_name("crop_panel.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        panel = mod.CropPanel(v, ctrl, crop_ctx)
        v.window.add_dock_widget(panel, name="Crop", area="right")
        v._canmac_panel = panel
    return v


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description="Preview the full_stack in napari and crop a box from it: draw the box, "
                    "set Z / time / channels in the Crop panel, press Crop.")
    ap.add_argument("t", type=int, help="Timepoint index to display (e.g. 0).")
    ap.add_argument("--store", default=None,
                    help="raw.zarr root (default: $CANMAC_FULLSTACK_ZARR, else full_stack/raw.zarr)")
    ap.add_argument("--level", type=int, default=None,
                    help="pyramid level to PREVIEW at; 0=native (default: 2, or the coarsest "
                         "level if the store has fewer). Crops are always written at level 0")
    ap.add_argument("--channels", default="all",
                    help="'all', or comma-separated names (candida,macrophage) / indices")
    ap.add_argument("--include-lysed", action="store_true", dest="include_lysed",
                    help="also show the lysed FF00FF channel (excluded by default, D-03)")
    for ax in "xyz":
        ap.add_argument(f"--{ax}0", type=int, default=None,
                        help=f"{ax} start in LEVEL-0 voxels (default: 0)")
        ap.add_argument(f"--{ax}1", type=int, default=None,
                        help=f"{ax} stop in LEVEL-0 voxels (default: full extent)")
    ap.add_argument("--3d", action="store_true", dest="three_d",
                    help="open in 3D (volume rendering) instead of 2D slices. Needs working "
                         "OpenGL: on a remote desktop use vglrun, or LIBGL_ALWAYS_SOFTWARE=1")
    ap.add_argument("--no-panel", action="store_true", dest="no_panel",
                    help="view only: no Crop panel")
    ap.add_argument("--check", action="store_true",
                    help="print the size table only, do not open napari.")
    args = ap.parse_args(argv)
    args.store = user_path(args.store, DEFAULT_STORE)

    if not os.path.isdir(args.store):
        sys.exit(f"ERROR: no such store: {args.store}")

    levels, axes, omero = read_multiscale(args.store)
    shape0 = levels[0]["shape"]
    if args.level is None:
        args.level = min(2, len(levels) - 1)
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
    # place the box where it sits in the whole volume: napari world coords = absolute um
    nap_translate = [b[d][0] * lv["scale"][axes.index(d)] for d in spatial]
    print(f"\npreviewing level {args.level}: {sub.shape} (lazy)  "
          f"voxel {tuple(round(s, 4) for s in nap_scale)} um")

    extent = {d: axis_len(axes, shape0, d) for d in "zyx"}
    ctx = None if args.no_panel else {
        "store": args.store, "levels": levels, "axes": axes, "omero": omero,
        "chans": chans, "t": args.t, "n_t": n_t, "extent": extent, "um": um,
        "caller_cwd": str(CALLER_CWD), "include_lysed": args.include_lysed,
    }
    v = open_viewer(sub, chans, nap_scale, nap_translate, um, extent,
                    title=f"ROI preview  L{args.level}  t{args.t}",
                    ndisplay=3 if args.three_d else 2, crop_ctx=ctx)
    print("\nIn the viewer (2D): move the Z slider under the image; the top-left overlay")
    print("shows the cursor in LEVEL-0 voxels.")
    if ctx is not None:
        print("Crop panel (right): 'Draw box', drag a rectangle, set Z start/end on the")
        print("slices you want, choose time/channels/output, press Crop.")
    print("Keys: J / K over opposite corners also set the box. The 2D/3D button toggles a")
    print("3D view; if that crashes, your OpenGL can't volume-render (guide: vglrun or")
    print("LIBGL_ALWAYS_SOFTWARE=1).")
    import napari

    napari.run()


if __name__ == "__main__":
    main()
