#!/usr/bin/env python
"""Preview the full_stack OME-Zarr in napari and crop a box from it (the crop GUI).

Opens the whole timelapse at ONE pyramid level as lazy dask layers (only the planes on
screen are read), in 2D, with a t slider (and play button) and a z slider. Draw the crop box on the "crop box" layer, set its Z range, time
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
        self.t_range = None  # (t0, t1) the crop will take, t1 excluded; None = not set
        self.listeners = []  # fn(box, source)
        self.refresh = lambda: None  # redraws the overlay; set by open_viewer
        self._guard = False

    def current_z(self) -> int:
        return int(np.clip(round(self.v.dims.point[-3] / self.um["z"]), 0, self.extent["z"] - 1))

    def current_t(self) -> int:
        return int(round(self.v.dims.point[0])) if self.v.dims.ndim > 3 else 0

    def set_t_range(self, t0: int, t1: int):
        self.t_range = (t0, t1)
        self.recolor()
        self.refresh()

    def in_range(self) -> tuple:
        """(this slice inside the box's Z range, this timepoint inside the time range)"""
        z0, z1 = self.box["z"]
        t_ok = self.t_range is None or self.t_range[0] <= self.current_t() < self.t_range[1]
        return z0 <= self.current_z() < z1, t_ok

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
        """Yellow on slices inside the box's Z range (at timepoints inside the time range),
        grey elsewhere: the box's extent in Z and time at a glance."""
        if self.box is None or not len(self.layer.data):
            return
        inside = all(self.in_range())
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


class LoadError(Exception):
    """A dataset could not be opened. The message is written for the person at the GUI."""


def _is_store(d: pathlib.Path) -> bool:
    """bioformats2raw-layout OME-Zarr: <d>/0/.zattrs holds the multiscales."""
    f = d / "0" / ".zattrs"
    if not f.is_file():
        return False
    try:
        return "multiscales" in json.loads(f.read_text())
    except (OSError, ValueError):
        return False


def find_store(path) -> str:
    """Resolve whatever the user picked to the OME-Zarr store root, forgivingly.

    Accepts the store itself (``raw.zarr``), anything inside it (``raw.zarr/0``,
    ``raw.zarr/0/0``, a ``.zattrs`` file) or a folder just above it holding exactly one
    store (``data/demo`` -> ``data/demo/DEMO/raw.zarr``). Raises LoadError with a
    plain-language explanation otherwise.
    """
    text = str(path).strip().strip("'\"")
    if not text:
        raise LoadError("No folder given. Click Browse… or paste the path of the .zarr folder.")
    p = pathlib.Path(os.path.expanduser(text))
    if not p.is_absolute():
        p = CALLER_CWD / p
    p = p.resolve()
    if not p.exists():
        raise LoadError(f"{p} does not exist.")
    if p.is_file() and not any(part.endswith(".zarr") for part in p.parts):
        raise LoadError(
            f"{p.name} is a single file. The cropper reads an OME-Zarr folder (a folder "
            f"ending in .zarr, such as full_stack/raw.zarr). Convert CZI / TIFF files first: "
            f"see stage 1, Conversion, in the guide.")
    start = p if p.is_dir() else p.parent
    for d in [start, *start.parents][:5]:  # picked something inside the store
        if _is_store(d):
            return str(d)
    found = sorted({c.parent.parent for pat in ("*/0/.zattrs", "*/*/0/.zattrs")
                    for c in start.glob(pat) if _is_store(c.parent.parent)})
    if len(found) == 1:  # picked the folder above the store
        return str(found[0])
    if found:
        names = ", ".join(str(f.relative_to(start)) for f in found[:6])
        raise LoadError(f"{start} holds {len(found)} datasets ({names}"
                        f"{', …' if len(found) > 6 else ''}). Choose one of them.")
    if (start / ".zattrs").is_file():
        raise LoadError(f"{start} is an OME-Zarr image without the bioformats2raw '0/' level; "
                        f"only bioformats2raw-layout stores (folder/0/…) are supported.")
    raise LoadError(f"No OME-Zarr dataset found in {start}. Choose the folder ending in .zarr "
                    f"(it contains a folder called 0).")


def prepare(store: str, t: int = 0, level: int | None = None, channels: str = "all",
            include_lysed: bool = False, box_args: dict | None = None) -> dict:
    """Open ``store`` lazily at ``level`` / timepoint ``t``: everything a viewer needs.

    ``box_args`` ({"x0": .., "z1": ..}, None = full extent) limits the loaded region.
    Raises LoadError (never exits) so the GUI can show the problem and carry on.
    """
    import dask.array as da

    try:
        levels, axes, omero = read_multiscale(store)
    except (OSError, KeyError, ValueError, IndexError) as e:
        raise LoadError(f"Couldn't read the OME-Zarr metadata in {store}: {e}") from e
    if not set("zyx") <= set(axes):
        raise LoadError(f"{store} has axes '{axes}'; the cropper needs z, y and x.")
    shape0 = levels[0]["shape"]
    if level is None:
        level = min(2, len(levels) - 1)
    if not 0 <= level < len(levels):
        raise LoadError(f"Preview level {level} doesn't exist: this store has {len(levels)} "
                        f"level(s), 0..{len(levels) - 1}.")
    n_t = axis_len(axes, shape0, "t")
    if not 0 <= t < n_t:
        raise LoadError(f"Timepoint {t} doesn't exist: this store has {n_t} (0..{n_t - 1}).")
    ns = argparse.Namespace(**{f"{d}{e}": (box_args or {}).get(f"{d}{e}")
                               for d in "xyz" for e in "01"})
    try:
        chans = select_channels(store, channels, omero, include_lysed)
        box0 = resolve_box(ns, axes, shape0)
    except SystemExit as e:  # the CLI helpers exit with a message; the GUI shows it
        raise LoadError(str(e).removeprefix("ERROR: ")) from None
    except ValueError as e:  # e.g. an unknown channel name
        raise LoadError(str(e)) from None

    lv = levels[level]
    b = scale_box(box0, shape0, lv["shape"], axes)
    arr = da.from_zarr(store, component=f"0/{lv['path']}")  # lazy: nothing read yet
    # every timepoint, so napari gets a time slider; still lazy: only planes on screen are read
    idx = tuple(slice(None) if d in "tc" else slice(*b[d]) for d in axes)
    sub = arr[idx]
    if "t" not in axes:
        sub = sub[None]
    if "c" not in axes:
        sub = sub[:, None]
    spatial = [d for d in axes if d in "zyx"]
    return {
        "store": store, "levels": levels, "axes": axes, "omero": omero, "chans": chans,
        "shape0": shape0, "level": level, "t": t, "n_t": n_t, "box0": box0,
        "box_given": any(v is not None for v in vars(ns).values()),
        "sub": sub,  # (T, C, Z, Y, X); ``t`` is the timepoint shown first
        "nap_scale": [lv["scale"][axes.index(d)] for d in spatial],
        # place the box where it sits in the whole volume: napari world coords = absolute um
        "nap_translate": [b[d][0] * lv["scale"][axes.index(d)] for d in spatial],
        "um": {d: levels[0]["scale"][axes.index(d)] for d in "xyz"},
        "extent": {d: axis_len(axes, shape0, d) for d in "zyx"},
    }


def describe(prep: dict, log=print) -> None:
    """The terminal summary + per-level Blender size table for a prepared dataset."""
    levels, axes, shape0, chans = prep["levels"], prep["axes"], prep["shape0"], prep["chans"]
    um, box0 = prep["um"], prep["box0"]
    log(f"store  : {prep['store']}")
    log(f"axes   : {axes}   levels: {len(levels)}   level 0: {shape0}")
    log(f"t      : {prep['t']} of {prep['n_t']}")
    log("channel: " + ", ".join(f"{i}:{lab}({col})" for i, lab, col in chans))
    log("\nROI box (level-0 voxels):")
    for d in "xyz":
        lo, hi = box0[d]
        log(f"  {d}: [{lo}:{hi}]  {hi - lo} vox  {(hi - lo) * um[d]:.1f} um")
    log("\nthis ROI as a float32 VDB (per channel, per timepoint):")
    log(f"  {'level':>5}  {'z':>5} {'y':>5} {'x':>6}  {'GiB':>7}  vs Blender {BLENDER_LIMIT_GIB:g} GiB")
    for i, lv in enumerate(levels):
        b = scale_box(box0, shape0, lv["shape"], axes)
        d = {k: b[k][1] - b[k][0] for k in "xyz"}
        gib = d["z"] * d["y"] * d["x"] * VDB_BYTES_PER_VOXEL / 2 ** 30
        verdict = "fits" if gib <= BLENDER_LIMIT_GIB else f"OVER by {gib / BLENDER_LIMIT_GIB:.1f}x"
        mark = "  <-- previewing" if i == prep["level"] else ""
        log(f"  {i:>5}  {d['z']:>5} {d['y']:>5} {d['x']:>6}  {gib:>7.2f}  {verdict:<14}{mark}")
    log(f"\n  {len(chans)} channel(s) -> {len(chans)} separate volume objects in Microscopy Nodes")


_PANEL_MOD = None


def _panel_module():
    """crop_panel.py, loaded once by path (scripts/cropping is not a package)."""
    global _PANEL_MOD
    if _PANEL_MOD is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_crop_panel", pathlib.Path(__file__).resolve().with_name("crop_panel.py"))
        _PANEL_MOD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_PANEL_MOD)
    return _PANEL_MOD


def reset_viewer(v) -> None:
    """Undo open_viewer on ``v`` (callbacks, layers, Crop dock) so another dataset can load."""
    for undo in reversed(getattr(v, "_canmac_cleanup", [])):
        try:
            undo()
        except Exception:  # noqa: BLE001 - best effort; a stale hook must not block loading
            pass
    v._canmac_cleanup = []
    v.layers.clear()
    for attr in ("_canmac_box", "_canmac_panel", "_canmac_pick"):
        if hasattr(v, attr):
            delattr(v, attr)


def open_viewer(sub, chans, nap_scale, nap_translate, um: dict, extent: dict,
                title: str = "ROI preview", show: bool = True, ndisplay: int = 2,
                crop_ctx: dict | None = None, viewer=None, t: int = 0):
    """Fill a napari viewer: one layer per channel, a live level-0 cursor readout, an
    editable "crop box" shapes layer, J / K corner picking and (with ``crop_ctx``) the Crop
    panel that writes the crop. Draw, J / K and the panel all edit one box.

    Layers carry ``scale`` (um/voxel at the previewed level) and ``translate`` (the box's
    offset in um), so napari WORLD coordinates are absolute micrometres in the whole
    volume and ``world_um / um[d]`` is the level-0 voxel index whatever box or level is
    on screen. ``sub`` is (T, C, Z, Y, X): the first slider is time (starting at ``t``),
    so a timelapse can be played; the second is Z. A new viewer is made unless ``viewer`` is given (it is then emptied and
    refilled: how the Dataset panel switches datasets). Unshown when ``show=False``.

    Opens in 2D by default: boxes are drawn in 2D, and 2D needs no 3D textures, so it
    also works on remote desktops whose OpenGL segfaults on volume rendering.
    """
    import napari
    from napari.utils.colormaps import Colormap

    if viewer is None:
        v = napari.Viewer(ndisplay=ndisplay, title=title, show=show)
    else:
        v = viewer
        reset_viewer(v)
        v.title = title
    v._canmac_cleanup = []
    undo = v._canmac_cleanup.append

    for i, label, color in chans:
        kw = {}
        if color:
            try:
                rgb = [int(color[k:k + 2], 16) / 255 for k in (0, 2, 4)]
                kw["colormap"] = Colormap([[0, 0, 0], rgb], name=f"c{i}")
            except (ValueError, IndexError):
                pass
        v.add_image(sub[:, i], name=f"{label}", scale=[1, *nap_scale],
                    translate=[0, *nap_translate], blending="additive", rendering="mip",
                    units=(None, *("um",) * len(nap_scale)), **kw)
    v.scale_bar.visible = True  # reads um from the layers' units (scale_bar.unit is a no-op)
    v.dims.axis_labels = ("t", "z", "y", "x")
    v.dims.set_point(0, t)
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
    undo(lambda: v.dims.events.point.disconnect(ctrl.recolor))
    v.layers.selection.active = v.layers[0]  # image active: J / K and panning work at once

    picked: dict = {}

    def cursor_vox0() -> dict:
        pos = list(v.cursor.position)[-3:]  # world um, (z, y, x)
        return {d: int(np.clip(round(pos[k] / um[d]), 0, extent[d] - 1))
                for k, d in enumerate("zyx")}

    def refresh(*_):
        v.axes.visible = v.dims.ndisplay == 3  # in 2D the axes sit on top of this overlay
        c = cursor_vox0()
        lines = [f"cursor (level-0 voxels)   x {c['x']}   y {c['y']}   z {c['z']}   "
                 f"t {ctrl.current_t()}"]
        if v.dims.ndisplay == 3:
            lines.append("3D view: positions are approximate - switch to 2D to draw the box")
        for key in ("1", "2"):
            if key in picked:
                p = picked[key]
                lines.append(f"corner {key}:  x {p['x']}  y {p['y']}  z {p['z']}")
        if ctrl.box is not None:
            z0, z1 = ctrl.box["z"]
            z_ok, t_ok = ctrl.in_range()
            where = "inside" if z_ok else "OUTSIDE"
            lines.append(f"box: {box_flags(ctrl.box)}   (this slice is {where} z {z0}-{z1 - 1})")
            if ctrl.t_range is not None and not t_ok:
                a, b = ctrl.t_range
                lines.append(f"timepoint {ctrl.current_t()} is OUTSIDE the crop's time range "
                             f"{a}-{b - 1}")
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

    ctrl.refresh = refresh
    ctrl.listeners.append(lambda box, source: refresh())
    v.dims.events.point.connect(refresh)
    undo(lambda: v.dims.events.point.disconnect(refresh))
    # J / K, not 1 / 2: the active layer's keymap wins over the viewer's, and napari
    # binds 1 / 2 on image layers to pan-zoom / transform mode.
    v.bind_key("j", lambda _v: pick("1"), overwrite=True)
    v.bind_key("k", lambda _v: pick("2"), overwrite=True)
    v.mouse_move_callbacks.append(refresh)
    undo(lambda: v.mouse_move_callbacks.remove(refresh))
    v.dims.events.ndisplay.connect(refresh)
    undo(lambda: v.dims.events.ndisplay.disconnect(refresh))
    v.text_overlay.visible = True
    v.text_overlay.position = "top_left"
    v.text_overlay.font_size = 10
    refresh()
    v._canmac_pick = pick  # exposed for tests
    v._canmac_box = ctrl
    if viewer is not None:
        v.reset_view()

    if crop_ctx is not None:
        from qtpy.QtWidgets import QFrame, QScrollArea

        panel = _panel_module().CropPanel(v, ctrl, crop_ctx)
        scroll = QScrollArea()  # scrolls on short screens instead of squashing the controls
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(panel)
        from qtpy.QtCore import Qt as _Qt
        scroll.setHorizontalScrollBarPolicy(_Qt.ScrollBarAlwaysOff)  # vertical only
        scroll.setMinimumWidth(panel.minimumSizeHint().width()
                               + scroll.verticalScrollBar().sizeHint().width())
        dock = v.window.add_dock_widget(scroll, name="Crop", area="right")
        v._canmac_panel = panel
        undo(lambda: v.window.remove_dock_widget(dock))
        ds_dock = getattr(v, "_canmac_dataset_dock", None)
        if ds_dock is not None:  # Dataset panel only as tall as it needs; the rest for Crop
            from qtpy.QtCore import Qt
            v.window._qt_window.resizeDocks(
                [ds_dock, dock], [ds_dock.widget().sizeHint().height() + 30, 10_000], Qt.Vertical)
    return v


def launch_gui(store: str | None = None, t: int = 0, level: int | None = None,
               channels: str = "all", include_lysed: bool = False,
               box_args: dict | None = None, ndisplay: int = 2, panel: bool = True,
               show: bool = True):
    """napari with the Dataset panel (choose / switch the store, timepoint, preview level)
    and, once a dataset is open, the Crop panel. ``store`` may be None: the window opens
    empty and asks for one. Box flags pre-fill the crop box for the store they came with."""
    import napari

    mod = _panel_module()
    v = napari.Viewer(ndisplay=ndisplay, title="macro_candi · crop", show=show)
    first = {"store": None, "box_args": box_args}

    def load(path, t_req, level_req):
        store_ = find_store(path)
        same = getattr(v, "_canmac_store", None) == store_
        if first["store"] is None:
            first["store"] = store_
        args = first["box_args"] if store_ == first["store"] else None
        keep = v._canmac_box.box if same and hasattr(v, "_canmac_box") else None
        prep = prepare(store_, 0 if t_req is None else t_req, level_req, channels,
                       include_lysed, args)
        print()
        describe(prep)
        ctx = None if not panel else {
            "store": store_, "levels": prep["levels"], "axes": prep["axes"],
            "omero": prep["omero"], "chans": prep["chans"], "t": prep["t"],
            "n_t": prep["n_t"], "extent": prep["extent"], "um": prep["um"],
            "caller_cwd": str(CALLER_CWD), "include_lysed": include_lysed,
        }
        open_viewer(prep["sub"], prep["chans"], prep["nap_scale"], prep["nap_translate"],
                    prep["um"], prep["extent"], viewer=v, crop_ctx=ctx, t=prep["t"],
                    title=f"{pathlib.Path(store_).name} · level {prep['level']}")
        v._canmac_store = store_
        if keep:
            v._canmac_box.set_box(keep, "code")
        elif prep["box_given"]:
            v._canmac_box.set_box(prep["box0"], "code")
        mod.remember(store_)
        return prep

    ds = mod.DatasetPanel(v, load, t=t, level=level)
    v._canmac_dataset_dock = v.window.add_dock_widget(ds, name="Dataset", area="right")
    v._canmac_dataset = ds
    if store:
        ds.open_path(store, t=t, level=level)
    else:
        ds.waiting()
    return v


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description="Preview an OME-Zarr in napari and crop a box from it: choose the dataset "
                    "in the Dataset panel (or pass --store), draw the box, press Crop.")
    ap.add_argument("t", type=int, nargs="?", default=0,
                    help="Timepoint shown first (default 0); the time slider shows the rest.")
    ap.add_argument("--store", default=None,
                    help="raw.zarr root (default: $CANMAC_FULLSTACK_ZARR, else full_stack/raw.zarr; "
                         "if neither exists the GUI asks you to choose a folder)")
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
    explicit = args.store is not None
    store = user_path(args.store, DEFAULT_STORE)
    box_args = {f"{d}{e}": getattr(args, f"{d}{e}") for d in "xyz" for e in "01"}

    if args.check:
        try:
            prep = prepare(find_store(store), args.t, args.level, args.channels,
                           args.include_lysed, box_args)
        except LoadError as e:
            sys.exit(f"ERROR: {e}")
        describe(prep)
        print("\n--check: skipping napari GUI.")
        return

    if not explicit and not os.path.isdir(store):
        store = None  # no default dataset here: the GUI asks for one
    launch_gui(store, args.t, args.level, args.channels, args.include_lysed, box_args,
               ndisplay=3 if args.three_d else 2, panel=not args.no_panel)
    print("\nIn the viewer: choose / switch the dataset in the Dataset panel (right), draw the")
    print("box (Crop panel: Draw box), set Z with Start here / End here, press Crop.")
    print("The first slider under the image is time: press its play button to watch the movie.")
    print("The view is 2D; the 2D/3D button toggles 3D. If that crashes, your OpenGL can't")
    print("volume-render (guide: vglrun or LIBGL_ALWAYS_SOFTWARE=1).")
    import napari

    napari.run()


if __name__ == "__main__":
    main()
