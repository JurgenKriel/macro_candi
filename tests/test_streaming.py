"""Streaming reader tests (IO-01, D-04).

Assert the reader returns a LAZY, color-selected dask array over the level-0
store, that the v2 big-endian store reads transparently as uint16 (retires
RESEARCH assumption A1), and that a single-timepoint read never materializes the
4D array (the anti-OOM guarantee, exercised via ``tests/mem_probe.py`` under a
peak-RSS measurement).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import dask.array as da
import numpy as np
import pytest

from canmac.io.channels import discard_indices, resolve_channel
from canmac.io.reader import get_view

REPO_ROOT = Path(__file__).resolve().parent.parent
MEM_PROBE = REPO_ROOT / "tests" / "mem_probe.py"


# --- Task 1: lazy streaming reader ------------------------------------------


def test_get_view_full_stack_is_lazy_dask(roi2_zarr):
    """get_view(dataset, channel) is a lazy (T,Z,Y,X) dask array (C sliced out)."""
    view = get_view("ROI2", "candida")
    assert isinstance(view, da.Array)
    assert hasattr(view, "compute")  # dask, NOT a materialized numpy array
    assert not isinstance(view, np.ndarray)
    assert view.shape == (120, 301, 401, 369)


def test_get_view_single_timepoint_is_lazy_zyx(roi2_zarr):
    """get_view(..., t=10) is a lazy (Z,Y,X) dask array, not yet materialized."""
    view = get_view("ROI2", "candida", t=10)
    assert isinstance(view, da.Array)
    assert hasattr(view, "compute")
    assert not isinstance(view, np.ndarray)
    assert view.shape == (301, 401, 369)


def test_from_zarr_reads_v2_bigendian_as_uint16(roi2_zarr):
    """A1 retired: v2, big-endian >u2, dimension_separator "/" reads transparently.

    The store reports big-endian byte order (>u2); the truthful "uint16" check is
    unsigned kind + 2-byte itemsize (numpy's ``== np.uint16`` is byte-order
    sensitive and would be False for >u2 — see SUMMARY).
    """
    arr = da.from_zarr(str(roi2_zarr), component="0/0")
    assert arr.shape == (120, 3, 301, 401, 369)
    assert arr.dtype.kind == "u"
    assert arr.dtype.itemsize == 2


def test_get_view_channel_indexing_matches_direct_zarr_read(roi2_zarr, roi2_zattrs):
    """A computed single-t volume equals a direct [t,c] zarr read (channel correctness)."""
    t = 5
    c = resolve_channel(str(roi2_zattrs), "macrophage")
    got = get_view("ROI2", "macrophage", t=t).compute()
    direct = da.from_zarr(str(roi2_zarr), component="0/0")[t, c].compute()
    assert got.shape == (301, 401, 369)
    assert got.dtype.kind == "u" and got.dtype.itemsize == 2
    assert np.array_equal(got, direct)


def test_get_view_resolves_by_color_never_lysed(roi2_zattrs):
    """candida/macrophage resolve to non-lysed indices; lysed FF00FF never selected."""
    lysed = discard_indices(str(roi2_zattrs))
    assert resolve_channel(str(roi2_zattrs), "candida") not in lysed
    assert resolve_channel(str(roi2_zattrs), "macrophage") not in lysed


def test_get_view_roi7_macrophage_shape(roi7_zarr):
    """ROI7 has different XY (291x475); a single-t read materializes exactly (Z,Y,X)."""
    got = get_view("ROI7", "macrophage", t=0).compute()
    assert got.shape == (301, 291, 475)
    assert got.dtype.kind == "u" and got.dtype.itemsize == 2


# --- Task 2: memory-profile proof (single-(t,c) read stays far below 32 GB) ---


def _run_mem_probe(dataset: str, t: int, channel: str):
    """Run mem_probe under /usr/bin/time -v; return (stdout, MaxRSS_kbytes).

    Falls back to resource.getrusage(RUSAGE_CHILDREN) if /usr/bin/time -v is
    unavailable. Skips if the dataset store is absent.
    """
    store = REPO_ROOT / dataset / "raw.zarr"
    if not store.exists():
        pytest.skip(f"data not present: {store}")

    cmd = [sys.executable, str(MEM_PROBE), "--dataset", dataset, "--t", str(t),
           "--channel", channel]

    if Path("/usr/bin/time").exists():
        proc = subprocess.run(
            ["/usr/bin/time", "-v", *cmd],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, f"mem_probe failed:\n{proc.stdout}\n{proc.stderr}"
        m = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", proc.stderr)
        assert m, f"could not parse MaxRSS from:\n{proc.stderr}"
        return proc.stdout, int(m.group(1))

    # Fallback: measure the child via RUSAGE_CHILDREN.
    import resource

    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"mem_probe failed:\n{proc.stdout}\n{proc.stderr}"
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss  # kbytes on Linux
    return proc.stdout, int(after - before) if after > before else after


def test_mem_probe_prints_single_volume_nbytes(roi2_zarr):
    """mem_probe computes exactly one (t,c) volume ~89 MB (301*401*369*2)."""
    stdout, _ = _run_mem_probe("ROI2", 60, "candida")
    m = re.search(r"nbytes[=:\s]+(\d+)", stdout)
    assert m, f"mem_probe did not print nbytes:\n{stdout}"
    nbytes = int(m.group(1))
    assert nbytes == 301 * 401 * 369 * 2  # ~89 MB single-(t,c) uint16 volume


def test_single_timepoint_read_maxrss_below_2gb(roi2_zarr):
    """MaxRSS of a single-(t,c) read < 2 GB (full-4D uint16 ~= 32 GB would blow past)."""
    _, maxrss_kb = _run_mem_probe("ROI2", 60, "candida")
    assert maxrss_kb < 2_000_000, f"MaxRSS {maxrss_kb} kB exceeds 2 GB budget"
