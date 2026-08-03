"""Unit tests for canmac.io.manifest — 2-dataset manifest + validating loader.

Enforces D-01 (exactly 2 datasets, no scene/tile machinery) and D-02 (timepoint
is the parallelism unit), cross-checking declared XY dims against the on-disk
`.zarray` shapes and failing loud on any drift (IO-04).
"""

from __future__ import annotations

import textwrap

import pytest

from canmac.io.manifest import datasets, get, load_manifest

# The real manifest loader touches the on-disk raw.zarr stores; skip cleanly if
# the scratch data is absent so collection still succeeds.
_needs_data = pytest.mark.usefixtures("roi2_zarr", "roi7_zarr")


@_needs_data
def test_manifest_enumerates_exactly_two_datasets(repo_root):
    ds = datasets(repo_root / "manifest.yaml")
    assert len(ds) == 2
    assert {d["id"] for d in ds} == {"ROI2", "ROI7"}


@_needs_data
def test_roi2_dims_cross_checked_against_zarray(repo_root):
    d = get("ROI2", repo_root / "manifest.yaml")
    assert d["dims"]["Y"] == 401
    assert d["dims"]["X"] == 369
    assert d["parallel_unit"] == "timepoint"
    assert d["dims"]["T"] == 120


@_needs_data
def test_roi7_dims_cross_checked_against_zarray(repo_root):
    d = get("ROI7", repo_root / "manifest.yaml")
    assert d["dims"]["Y"] == 291
    assert d["dims"]["X"] == 475
    assert d["parallel_unit"] == "timepoint"


@_needs_data
def test_channels_recorded_by_color(repo_root):
    for d in datasets(repo_root / "manifest.yaml"):
        assert d["channels"]["candida"] == "FF0000"
        assert d["channels"]["macrophage"] == "00FF00"
        assert "FF00FF" in d["channels"]["discard"]


def _write_manifest(tmp_path, body: str):
    p = tmp_path / "manifest.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_wrong_dataset_count_raises(tmp_path):
    # A 50-"scene"-style count (or any count != 2) must fail loud.
    p = _write_manifest(
        tmp_path,
        """
        datasets:
          - id: ROI2
            zarr_path: ROI2/raw.zarr
            component: "0/0"
            dims: {T: 120, C: 3, Z: 301, Y: 401, X: 369}
            channels: {candida: FF0000, macrophage: 00FF00, discard: [FF00FF]}
            parallel_unit: timepoint
        """,
    )
    with pytest.raises(ValueError):
        load_manifest(p)


def test_missing_store_raises(tmp_path):
    # zarr_path pointing at a non-existent store must raise, not pass silently.
    p = _write_manifest(
        tmp_path,
        """
        datasets:
          - id: ROI2
            zarr_path: nope/ROI2/raw.zarr
            component: "0/0"
            dims: {T: 120, C: 3, Z: 301, Y: 401, X: 369}
            channels: {candida: FF0000, macrophage: 00FF00, discard: [FF00FF]}
            parallel_unit: timepoint
          - id: ROI7
            zarr_path: nope/ROI7/raw.zarr
            component: "0/0"
            dims: {T: 120, C: 3, Z: 301, Y: 291, X: 475}
            channels: {candida: FF0000, macrophage: 00FF00, discard: [FF00FF]}
            parallel_unit: timepoint
        """,
    )
    with pytest.raises(ValueError):
        load_manifest(p)


@_needs_data
def test_dims_disagreeing_with_zarray_raises(tmp_path, repo_root):
    # Declared Y that disagrees with the actual .zarray shape must fail loud.
    store2 = (repo_root / "ROI2" / "raw.zarr").resolve()
    store7 = (repo_root / "ROI7" / "raw.zarr").resolve()
    p = _write_manifest(
        tmp_path,
        f"""
        datasets:
          - id: ROI2
            zarr_path: {store2}
            component: "0/0"
            dims: {{T: 120, C: 3, Z: 301, Y: 999, X: 369}}
            channels: {{candida: FF0000, macrophage: 00FF00, discard: [FF00FF]}}
            parallel_unit: timepoint
          - id: ROI7
            zarr_path: {store7}
            component: "0/0"
            dims: {{T: 120, C: 3, Z: 301, Y: 291, X: 475}}
            channels: {{candida: FF0000, macrophage: 00FF00, discard: [FF00FF]}}
            parallel_unit: timepoint
        """,
    )
    with pytest.raises(ValueError):
        load_manifest(p)
