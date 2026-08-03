"""Shared pytest fixtures for the canmac test suite.

Fixtures point at the read-only ``ROI{2,7}/raw.zarr`` inputs and NEVER
materialize the 4D array. When the data is absent (e.g. CI without the scratch
volumes) data-dependent fixtures ``pytest.skip`` so collection still succeeds.
Downstream Phase 1 plans (channels/manifest/calibration/bleach) build on these.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Repo root = parent of this tests/ directory.
REPO_ROOT = Path(__file__).resolve().parent.parent


def pytest_configure(config):
    """Register custom markers so ``@pytest.mark.gpu`` never warns.

    The GPU-gated integration tests (``test_smoke.py``, ``test_segment_gpu.py``)
    carry ``@pytest.mark.gpu``; registering it here keeps ``--strict-markers`` and
    the default marker-warning path quiet.
    """
    config.addinivalue_line("markers", "gpu: requires a CUDA GPU node")


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"data not present: {path}")
    return path


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def roi2_zarr() -> Path:
    """Path to the ROI2 OME-Zarr store (bioformats2raw layout 3, Zarr v2)."""
    return _require(REPO_ROOT / "ROI2" / "raw.zarr")


@pytest.fixture
def roi7_zarr() -> Path:
    """Path to the ROI7 OME-Zarr store."""
    return _require(REPO_ROOT / "ROI7" / "raw.zarr")


@pytest.fixture
def roi2_zattrs() -> Path:
    """Path to the ROI2 NGFF group ``.zattrs`` (multiscales + omero)."""
    return _require(REPO_ROOT / "ROI2" / "raw.zarr" / "0" / ".zattrs")


@pytest.fixture
def one_volume():
    """Return a helper that reads ONE (t, c) plane lazily via dask.

    Usage in later plans::

        vol = one_volume(store, t=10, c=1)   # lazy (Z, Y, X)
        arr = vol.compute()                  # ~89 MB uint16, never the 4D array

    Reads only the requested (t, c) slice from the level-0 array ``0/0``; the
    full T x C x Z x Y x X array is never materialized (D-04).
    """
    import dask.array as da

    def _one_volume(store, t: int, c: int):
        arr = da.from_zarr(str(store), component="0/0")  # lazy (T,C,Z,Y,X)
        return arr[t, c]  # lazy (Z,Y,X); caller .compute()s this single plane

    return _one_volume


@pytest.fixture
def synthetic_labels():
    """A small ``(Z,Y,X)=(8,16,16)`` uint16 label volume with THREE instances.

    Three spatially separated cubes carry instance IDs 1, 2, 3 on a background of
    0, so ``labels.max() == 3`` and ``(labels > 0).sum() > 0``. Used by the
    label-store round-trip and object-census CPU unit tests (no GPU, no disk).
    """
    import numpy as np

    labels = np.zeros((8, 16, 16), dtype=np.uint16)
    labels[1:3, 1:4, 1:4] = 1  # instance 1
    labels[1:3, 1:4, 8:11] = 2  # instance 2 (separated in X)
    labels[5:7, 8:11, 8:11] = 3  # instance 3 (separated in Z and Y)
    return labels


@pytest.fixture
def tmp_results(tmp_path):
    """A tmp dir usable as the ``out_dir="results/{roi}"`` root.

    Yields a writable ``results/`` directory under pytest's ``tmp_path`` so label
    stores and census shards written by the tests never touch the real
    ``results/`` tree.
    """
    d = tmp_path / "results"
    d.mkdir()
    yield d
