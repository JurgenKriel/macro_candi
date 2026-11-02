#!/usr/bin/env python
"""Regenerate the napari screenshots in docs/assets/cropping/ from the synthetic demo.

Drives the real crop GUI (12_crop_roi.py --gui) with real Qt mouse clicks, drags and key
presses, so the pictures show exactly what a user gets and break loudly if the GUI changes.

Needs a display. On a headless node, use a virtual one with software OpenGL:
    python -m canmac.demo generate                      # once: data/demo/DEMO/raw.zarr
    module load Xvfb; Xvfb :99 -screen 0 1920x1200x24 &
    DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1 pixi run python scripts/docs/cropping_screenshots.py
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import tempfile
import time

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
STORE = str(REPO / "data/demo/DEMO/raw.zarr")
OUT = REPO / "docs/assets/cropping"
T = 12
WINDOW = (1700, 1000)
UM = 0.5  # demo voxel size


def _load(name: str):
    cwd = os.getcwd()
    spec = importlib.util.spec_from_file_location(name, REPO / f"scripts/cropping/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    os.chdir(cwd)
    return mod


def pump(n: int = 10):
    from qtpy.QtWidgets import QApplication
    for _ in range(n):
        QApplication.processEvents()


def launch(module, argv: list):
    """Run a script's main() for real, minus the blocking event loop; return the viewer."""
    import napari

    run, napari.run = napari.run, (lambda *a, **k: None)
    try:
        module.main(argv)
    finally:
        napari.run = run
    v = napari.current_viewer()
    v.window._qt_window.resize(*WINDOW)
    for layer in v.layers:  # drop the noise floor, as you would with the contrast slider
        if layer._type_string == "image":
            lo, hi = np.percentile(np.asarray(layer.data), [90, 99.95])
            layer.contrast_limits = (float(lo), float(hi))
    pump(20)
    return v


def drag(v, a_um, b_um):
    """A real left-button drag on the canvas between two (y, x) world positions."""
    from qtpy.QtCore import QEvent, QPoint, QPointF, Qt
    from qtpy.QtGui import QMouseEvent
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QApplication

    canvas = v.window._qt_viewer.canvas.native

    def px(y, x):
        cy, cx = v.camera.center[-2:]
        z = v.camera.zoom
        return QPoint(int(round(canvas.width() / 2 + (x - cx) * z)),
                      int(round(canvas.height() / 2 + (y - cy) * z)))

    pa, pb = px(*a_um), px(*b_um)
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, pa)
    for f in np.linspace(0, 1, 8)[1:]:
        p = QPointF(pa.x() + (pb.x() - pa.x()) * f, pa.y() + (pb.y() - pa.y()) * f)
        QApplication.sendEvent(canvas, QMouseEvent(QEvent.MouseMove, p, Qt.NoButton,
                                                   Qt.LeftButton, Qt.NoModifier))
        pump(2)
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, pb)
    pump(20)


def click(button):
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest
    QTest.mouseClick(button, Qt.LeftButton)
    pump()


def shot(v, name: str):
    from qtpy.QtWidgets import QApplication
    from skimage.io import imsave

    pump(10)
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
    for old in OUT.glob("*.png"):
        old.unlink()
    crop_mod = _load("12_crop_roi")
    preview_mod = _load("11_preview_crop")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cropshots_"))

    # 1. the GUI in 3D (--3d): a look at the whole volume first
    v = launch(crop_mod, ["--gui", "--store", STORE, "--t0", str(T)])
    v.dims.ndisplay = 3
    v.camera.angles = (-15, 25, 80)
    shot(v, "01-overview-3d.png")

    # 2. 2D, Draw box, drag a rectangle; Z range via Start here / End here
    v.dims.ndisplay = 2
    pump(10)
    panel = v._canmac_panel
    click(panel.draw_btn)
    drag(v, (12 * UM - UM / 2, 10 * UM - UM / 2), (80 * UM - UM / 2, 90 * UM - UM / 2))
    v.dims.set_point(0, 8 * UM); pump()
    click(panel.z_start)
    v.dims.set_point(0, 41 * UM); pump()
    click(panel.z_end)
    v.layers.selection.active = v.layers[0]
    v.dims.set_point(0, 25 * UM)
    v.cursor.position = (25 * UM, 34 * UM, 33 * UM)
    v.mouse_move_callbacks[-1](v, None)  # the overlay refreshes on mouse move; no mouse here
    panel.t1.setValue(T + 6)
    panel.out.setText("crops/roi_a.zarr")
    panel._out_edited()
    shot(v, "02-draw-box.png")

    # 3. a slice outside the Z range: the box turns grey
    v.dims.set_point(0, 45 * UM)
    v.mouse_move_callbacks[-1](v, None)
    shot(v, "03-outside-z-range.png")
    v.dims.set_point(0, 25 * UM)

    # 4. Crop pressed and finished
    out = tmp / "roi_a.zarr"
    panel.out.setText(str(out))
    click(panel.crop_btn)
    t_end = time.time() + 300
    while panel.timer.isActive() and time.time() < t_end:
        pump(); time.sleep(0.05)
    panel.status.setText(panel.status.text().replace(str(tmp), "crops"))  # tidy path
    panel.out.setText("crops/roi_a.zarr")
    shot(v, "04-crop-done.png")
    v.close()

    # 5. the written crop, opened in the same viewer in 3D (coordinates restart at 0)
    v = launch(preview_mod, ["0", "--store", str(out), "--level", "0", "--3d", "--no-panel"])
    v.camera.angles = (-15, 25, 80)
    shot(v, "05-crop-result-3d.png")
    v.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
