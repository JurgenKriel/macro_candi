"""Cropping tools: box maths in 11_preview_crop.py and the shared writer in 12_crop_roi.py.

The napari GUI itself needs a display; everything it computes lives in these functions,
so they are tested here without one.
"""

import importlib.util
import json
import os
import pathlib

import numpy as np
import pytest

CROP_DIR = pathlib.Path(__file__).resolve().parents[1] / "scripts/cropping"


def _load(name):
    cwd = os.getcwd()  # the scripts chdir to the repo root on import
    try:
        spec = importlib.util.spec_from_file_location(name.replace(".", "_"), CROP_DIR / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.chdir(cwd)


EXTENT = {"z": 48, "y": 128, "x": 128}
UM = {"z": 0.5, "y": 0.5, "x": 0.5}


def test_corners_any_order_stop_exclusive():
    pc = _load("11_preview_crop")
    a = {"x": 99, "y": 15, "z": 20}
    b = {"x": 30, "y": 49, "z": 5}
    box = pc.corners_to_box(a, b, EXTENT)
    assert box == {"z": (5, 21), "y": (15, 50), "x": (30, 100)}
    assert pc.box_flags(box) == "--x0 30 --x1 100 --y0 15 --y1 50 --z0 5 --z1 21"


def test_corners_clamped_to_volume():
    pc = _load("11_preview_crop")
    box = pc.corners_to_box({"x": -3, "y": 0, "z": 0}, {"x": 500, "y": 127, "z": 47}, EXTENT)
    assert box == {"z": (0, 48), "y": (0, 128), "x": (0, 128)}


def test_same_corner_is_one_voxel():
    pc = _load("11_preview_crop")
    p = {"x": 7, "y": 8, "z": 9}
    assert pc.corners_to_box(p, p, EXTENT) == {"z": (9, 10), "y": (8, 9), "x": (7, 8)}


def test_rectangle_round_trips_through_world_um():
    pc = _load("11_preview_crop")
    box = {"z": (8, 42), "y": (12, 80), "x": (10, 90)}
    rect = pc.box_to_rect(box, UM)
    assert pc.shape_to_yx(rect, UM, EXTENT) == {"y": (12, 80), "x": (10, 90)}


def test_any_shape_uses_its_bounding_box_clamped():
    pc = _load("11_preview_crop")
    # a triangle partly outside the volume, vertices in world um (y, x)
    tri = [[-5.0, 10.0], [20.0, 80.0], [10.0, 30.0]]
    yx = pc.shape_to_yx(tri, UM, EXTENT)
    # x min 10 um is voxel 20's centre (included); y max 20 um is voxel 40's centre (included)
    assert yx == {"y": (0, 41), "x": (20, 128)}
    # a degenerate click still gives a 1-voxel box
    assert pc.shape_to_yx([[5.0, 5.0], [5.0, 5.0]], UM, EXTENT) == {"y": (10, 11), "x": (10, 11)}
    # non-round voxel sizes (the reference data's 0.145 um) still round-trip exactly
    um = {"z": 0.145, "y": 0.145, "x": 0.145}
    ext = {"z": 301, "y": 3000, "x": 3000}
    for lo, hi in ((0, 1), (7, 1888), (1199, 2700), (2999, 3000)):
        box = {"z": (0, 1), "y": (lo, hi), "x": (lo, hi)}
        assert pc.shape_to_yx(pc.box_to_rect(box, um), um, ext) == {"y": (lo, hi), "x": (lo, hi)}


def test_estimate_and_command():
    cr = _load("12_crop_roi")
    levels = [{"path": "0", "shape": (10, 2, 300, 2000, 2000), "scale": [1, 1, 0.145, 0.145, 0.145]}]
    box = {"z": (0, 300), "y": (0, 2000), "x": (0, 2000)}
    est = cr.estimate(levels, "tczyx", box, 0, n_chan=2, n_t=10)
    assert est["dims"] == {"x": 2000, "y": 2000, "z": 300}
    assert est["vdb_gib"] == pytest.approx(300 * 2000 * 2000 * 4 / 2 ** 30)
    assert not est["fits"]  # 4.47 GiB > 4
    cmd = cr.crop_command("/s.zarr", "/o.zarr", box, 0, 10, "all")
    assert "--x0 0 --x1 2000 --y0 0 --y1 2000 --z0 0 --z1 300 --t0 0 --t1 10" in cmd


@pytest.fixture(scope="module")
def demo_store(tmp_path_factory):
    from canmac import demo

    out = tmp_path_factory.mktemp("cropdemo")
    demo.generate(out)
    return out / demo.DATASET_ID / "raw.zarr"


def test_write_crop_matches_source_and_records_box(demo_store, tmp_path):
    import dask.array as da

    cr = _load("12_crop_roi")
    pc = cr._import_preview()
    levels, axes, omero = pc.read_multiscale(str(demo_store))
    chans = pc.select_channels(str(demo_store), "candida,macrophage", omero, False)
    box = {"z": (8, 42), "y": (12, 80), "x": (10, 90)}
    fracs = []
    out = str(tmp_path / "c.zarr")
    cr.write_crop(str(demo_store), out, levels, axes, box, 3, 9, chans,
                  progress=fracs.append, log=lambda m: None)

    src = da.from_zarr(str(demo_store), component="0/0")[3:9][:, [i for i, _, _ in chans],
                                                               8:42, 12:80, 10:90].compute()
    got = da.from_zarr(out, component="0/0").compute()
    assert np.array_equal(src, got)
    assert da.from_zarr(out, component="0/1").shape == (6, 2, 17, 34, 40)  # isotropic 2x
    assert da.from_zarr(out, component="0/2").shape == (6, 2, 8, 17, 20)
    meta = json.load(open(os.path.join(out, "0", ".zattrs")))["multiscales"][0]["metadata"]
    assert meta["canmac_crop"]["box_level0"] == {"z": [8, 42], "y": [12, 80], "x": [10, 90]}
    assert meta["canmac_crop"]["t"] == [3, 9]
    assert fracs[0] == 0.0 and fracs[-1] == 1.0 and fracs == sorted(fracs)

    with pytest.raises(FileExistsError):
        cr.write_crop(str(demo_store), out, levels, axes, box, 3, 9, chans, log=lambda m: None)



def test_find_store_is_forgiving_about_what_was_picked(demo_store):
    pc = _load("11_preview_crop")
    root = str(demo_store.resolve())
    # the store itself, anything inside it, or the folder just above it
    for picked in (demo_store, demo_store / "0", demo_store / "0" / "0", demo_store / "0" / ".zattrs",
                   demo_store.parent, demo_store.parent.parent, f"  '{demo_store}'  "):
        assert pc.find_store(picked) == root, picked


def test_find_store_explains_what_is_wrong(demo_store, tmp_path):
    import shutil

    pc = _load("11_preview_crop")
    with pytest.raises(pc.LoadError, match="does not exist"):
        pc.find_store(tmp_path / "nope")
    (tmp_path / "movie.czi").write_bytes(b"x")
    with pytest.raises(pc.LoadError, match="single file.*Convert CZI"):
        pc.find_store(tmp_path / "movie.czi")
    (tmp_path / "empty").mkdir()
    with pytest.raises(pc.LoadError, match="No OME-Zarr dataset found"):
        pc.find_store(tmp_path / "empty")
    for name in ("a.zarr", "b.zarr"):
        shutil.copytree(demo_store, tmp_path / "two" / name)
    with pytest.raises(pc.LoadError, match=r"holds 2 datasets \(a.zarr, b.zarr\)"):
        pc.find_store(tmp_path / "two")
    with pytest.raises(pc.LoadError, match="No folder given"):
        pc.find_store("  ")


def test_prepare_reports_bad_choices_instead_of_exiting(demo_store):
    pc = _load("11_preview_crop")
    store = str(demo_store)
    prep = pc.prepare(store, t=5, level=0, box_args={"x0": 10, "x1": 90})
    assert prep["n_t"] == 24 and prep["t"] == 5 and prep["box_given"]
    assert prep["box0"] == {"z": (0, 48), "y": (0, 128), "x": (10, 90)}
    assert prep["sub"].shape[1:] == (48, 128, 80)  # (C, Z, Y, X) of the box only, still lazy
    assert [name for _, name, _ in prep["chans"]] == ["candida", "macrophage"]  # lysed decoy skipped
    assert not pc.prepare(store)["box_given"]
    with pytest.raises(pc.LoadError, match="Timepoint 24 doesn't exist"):
        pc.prepare(store, t=24)
    with pytest.raises(pc.LoadError, match="Preview level 3 doesn't exist"):
        pc.prepare(store, level=3)
    with pytest.raises(pc.LoadError):
        pc.prepare(store, channels="nosuchchannel")
