"""Corner picking in 11_preview_crop.py: two level-0 corners -> cropper flags (no GUI)."""

import importlib.util
import os
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/cropping/11_preview_crop.py"


def _load():
    cwd = os.getcwd()  # the script chdirs to the repo root on import
    try:
        spec = importlib.util.spec_from_file_location("preview_crop", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.chdir(cwd)


EXTENT = {"z": 48, "y": 128, "x": 128}


def test_corners_any_order_stop_exclusive():
    pc = _load()
    a = {"x": 99, "y": 15, "z": 20}
    b = {"x": 30, "y": 49, "z": 5}
    box = pc.corners_to_box(a, b, EXTENT)
    assert box == {"z": (5, 21), "y": (15, 50), "x": (30, 100)}
    assert pc.box_flags(box) == "--x0 30 --x1 100 --y0 15 --y1 50 --z0 5 --z1 21"


def test_corners_clamped_to_volume():
    pc = _load()
    box = pc.corners_to_box({"x": -3, "y": 0, "z": 0}, {"x": 500, "y": 127, "z": 47}, EXTENT)
    assert box == {"z": (0, 48), "y": (0, 128), "x": (0, 128)}


def test_same_corner_is_one_voxel():
    pc = _load()
    p = {"x": 7, "y": 8, "z": 9}
    assert pc.corners_to_box(p, p, EXTENT) == {"z": (9, 10), "y": (8, 9), "x": (7, 8)}
