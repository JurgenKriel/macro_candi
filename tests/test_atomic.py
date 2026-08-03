"""Tests for canmac.io.atomic: temp -> os.replace + .done-written-last (T-01-00-04).

The integrity guarantee under test: an interrupted write leaves NO ``.done``
sentinel and NO ``.tmp.<pid>`` leftover shadowing the final path, so a killed
SLURM job never produces a present-but-incomplete artifact that downstream
stages mistake for finished output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from canmac.io import atomic


def test_atomic_write_then_done(tmp_path: Path):
    """A full write + write_done leaves the payload, the .done, and is_done True."""
    target = tmp_path / "calibration.json"
    payload = {"voxel_um": 0.145, "isotropic": True}

    atomic.atomic_write_json(target, payload)
    assert target.exists()
    assert json.loads(target.read_text()) == payload
    # .done must not exist until we explicitly write it last.
    assert not atomic.is_done(target)

    atomic.write_done(target)
    assert atomic.done_path(target).exists()
    assert atomic.is_done(target)


def test_interrupted_leaves_no_done(tmp_path: Path):
    """Payload written but the job dies before write_done -> no .done sentinel."""
    target = tmp_path / "bleach.json"

    # Simulate a job that wrote the payload and was then killed before write_done.
    atomic.atomic_write_json(target, {"t": 0, "factor": 1.0})

    assert target.exists()  # payload is present...
    assert not atomic.is_done(target)  # ...but is correctly NOT marked done
    assert not atomic.done_path(target).exists()


def test_no_partial_file_on_tmp_failure(tmp_path: Path, monkeypatch):
    """If the write fails mid-flight, the final path is absent and no .tmp lingers.

    We force ``os.replace`` to raise (simulating a crash between the temp write
    and the rename). The final path must not exist, and no ``.tmp.<pid>``
    leftover may shadow a future write.
    """
    import os as _os

    target = tmp_path / "sidecar.json"

    def boom(src, dst):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(atomic.os, "replace", boom)

    with pytest.raises(OSError):
        atomic.atomic_write_json(target, {"x": 1})

    # Final path never appeared...
    assert not target.exists()
    # ...and no temp sibling was left behind to shadow the final path.
    leftovers = list(tmp_path.glob("sidecar.json.tmp.*"))
    assert leftovers == [], f"stray temp files: {leftovers}"


def test_atomic_dir_replaces_on_success(tmp_path: Path):
    """atomic_dir renames the temp dir into place only on clean exit."""
    final = tmp_path / "store.zarr"

    with atomic.atomic_dir(final) as d:
        (d / "0").mkdir()
        (d / "0" / "chunk").write_text("data")
        assert not final.exists()  # not visible until the block exits cleanly

    assert final.exists()
    assert (final / "0" / "chunk").read_text() == "data"


def test_atomic_dir_cleans_up_on_error(tmp_path: Path):
    """atomic_dir removes the temp dir and leaves no final dir on error."""
    final = tmp_path / "store.zarr"

    with pytest.raises(RuntimeError):
        with atomic.atomic_dir(final) as d:
            (d / "partial").write_text("half")
            raise RuntimeError("killed mid-write")

    assert not final.exists()
    assert list(tmp_path.glob("store.zarr.tmp.*")) == []
