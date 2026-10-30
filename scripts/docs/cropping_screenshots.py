#!/usr/bin/env python
"""Regenerate the napari screenshots in docs/assets/cropping/ from the synthetic demo.

Drives the real viewer from scripts/cropping/11_preview_crop.py (same layers, overlay
and J/K corner picking a user gets), so the pictures stay honest when the tool changes.

Needs a display. On a headless node, use a virtual one with software OpenGL:
    python -m canmac.demo generate                      # once: data/demo/DEMO/raw.zarr
    module load Xvfb; Xvfb :99 -screen 0 1920x1200x24 &
    DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1 pixi run python scripts/docs/cropping_screenshots.py
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import types

REPO = pathlib.Path(__file__).resolve().parents[2]
STORE = str(REPO / "data/demo/DEMO/raw.zarr")
OUT = REPO / "docs/assets/cropping"
T = 12
BOX = dict(x0=10, x1=90, y0=12, y1=80, z0=8, z1=42)  # the box the guide walks through
WINDOW = (1500, 900)


def _load_preview():
    cwd = os.getcwd()
    spec = importlib.util.spec_from_file_location(
        "preview_crop", REPO / "scripts/cropping/11_preview_crop.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    os.chdir(cwd)
    return mod


def build(pc, level: int | None, box: dict | None, title: str):
    """Same data path as 11_preview_crop.main(): lazy box at a level, placed in world um."""
    import dask.array as da

    levels, axes, omero = pc.read_multiscale(STORE)
    shape0 = levels[0]["shape"]
    level = min(2, len(levels) - 1) if level is None else level
    args = types.SimpleNamespace(**{f"{d}{e}": None for d in "xyz" for e in "01"})
    for k, val in (box or {}).items():
        setattr(args, k, val)
    box0 = pc.resolve_box(args, axes, shape0)
    lv = levels[level]
    b = pc.scale_box(box0, shape0, lv["shape"], axes)
    arr = da.from_zarr(STORE, component=f"0/{lv['path']}")
    sub = arr[tuple(T if d == "t" else slice(None) if d == "c" else slice(*b[d]) for d in axes)]
    spatial = [d for d in axes if d in "zyx"]
    scale = [lv["scale"][axes.index(d)] for d in spatial]
    trans = [b[d][0] * lv["scale"][axes.index(d)] for d in spatial]
    um = {d: levels[0]["scale"][axes.index(d)] for d in "xyz"}
    extent = {d: pc.axis_len(axes, shape0, d) for d in "zyx"}
    chans = pc.select_channels(STORE, "all", omero, False)
    v = pc.open_viewer(sub, chans, scale, trans, um, extent, title=title, show=True)
    v.window._qt_window.resize(*WINDOW)
    import numpy as np

    for layer in v.layers:  # drop the noise floor, as you would with the contrast slider
        lo, hi = np.percentile(np.asarray(layer.data), [90, 99.95])
        layer.contrast_limits = (float(lo), float(hi))
    return v, um


def press(v, key: str):
    """A real key press on the canvas, so the screenshots exercise the actual bindings."""
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QApplication

    QApplication.processEvents()
    QTest.keyClick(v.window._qt_viewer.canvas.native, getattr(Qt, f"Key_{key.upper()}"))
    QApplication.processEvents()


def shot(v, name: str):
    from qtpy.QtWidgets import QApplication
    from skimage.io import imsave

    for _ in range(5):
        QApplication.processEvents()
    try:  # hide toasts (e.g. backend deprecation notices) that would cover the canvas
        from napari._qt.dialogs.qt_notification import NapariQtNotification
        for w in QApplication.allWidgets():
            if isinstance(w, NapariQtNotification):
                w.hide()
    except ImportError:
        pass
    img = v.screenshot(canvas_only=False, flash=False)
    imsave(OUT / name, img[..., :3], check_contrast=False)
    print("wrote", OUT / name, img.shape)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pc = _load_preview()

    # 1. whole volume, 3D, as it opens
    v, um = build(pc, None, None, f"ROI preview  L0  t{T}")
    v.camera.angles = (-15, 25, 80)
    v.camera.zoom *= 0.95
    shot(v, "01-overview-3d.png")

    # 2. 2D mode, before picking: the overlay tracks the cursor in level-0 voxels
    v.dims.ndisplay = 2
    v.dims.set_point(0, 25 * um["z"])
    v.cursor.position = (25 * um["z"], 34 * um["y"], 33 * um["x"])
    v.mouse_move_callbacks[-1](v, None)  # the overlay refreshes on mouse move; no mouse here
    shot(v, "02-slice-2d.png")

    # 3. J on one corner, K on the opposite one (different Z): box drawn, flags shown
    a = dict(x=BOX["x0"], y=BOX["y0"], z=BOX["z0"])
    b = dict(x=BOX["x1"] - 1, y=BOX["y1"] - 1, z=BOX["z1"] - 1)
    for key, c in (("j", a), ("k", b)):
        v.dims.set_point(0, c["z"] * um["z"])
        v.cursor.position = (c["z"] * um["z"], c["y"] * um["y"], c["x"] * um["x"])
        press(v, key)
    v.dims.set_point(0, 25 * um["z"])
    shot(v, "03-pick-corners-2d.png")
    v.close()

    # 4. re-opened with the picked box at full resolution
    v, _ = build(pc, 0, BOX, f"ROI preview  L0  t{T}")
    v.camera.angles = (-15, 25, 80)
    shot(v, "04-confirm-box-3d.png")
    v.close()


if __name__ == "__main__":
    main()
