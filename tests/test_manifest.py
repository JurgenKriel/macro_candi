"""Unit tests for canmac.io.manifest — the validating dataset-manifest loader.

The manifest may list any number of datasets with any ids/T/colours; what is enforced
is structure: unique filesystem-safe ids, hex colours, D-02 (timepoint is the
parallelism unit) and declared dims cross-checked against the on-disk ``.zarray``
shape, failing loud on any drift (IO-04). Reference-data tests skip without ROI2/ROI7.
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


def _fake_store(root, name, shape=(24, 3, 8, 16, 16)):
    """A minimal store: only ``<name>/raw.zarr/0/0/.zarray`` (all the validator reads)."""
    import json

    d = root / name / "raw.zarr" / "0" / "0"
    d.mkdir(parents=True)
    (d / ".zarray").write_text(json.dumps({"shape": list(shape)}))
    return f"{name}/raw.zarr"


_REC = """
  - id: {id}
    zarr_path: {path}
    dims: {{T: 24, C: 3, Z: 8, Y: 16, X: 16}}
    channels: {{candida: {cand}, macrophage: 00FF00, discard: [FF00FF]}}
"""


def _manifest(tmp_path, *records):
    return _write_manifest(tmp_path, "datasets:" + "".join(records))


def test_any_dataset_id_and_count_accepted(tmp_path):
    # Not tied to the reference experiment: one dataset, any id, any T.
    p = _manifest(tmp_path, _REC.format(id="exp-01", path=_fake_store(tmp_path, "a"), cand="ff0000"))
    (d,) = datasets(p)
    assert d["id"] == "exp-01"
    assert d["dims"]["T"] == 24
    assert d["channels"]["candida"] == "FF0000"  # normalised to upper case
    assert d["component"] == "0/0" and d["parallel_unit"] == "timepoint"  # defaults


def test_empty_dataset_list_raises(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        load_manifest(_write_manifest(tmp_path, "datasets: []\n"))


def test_duplicate_ids_raise(tmp_path):
    path = _fake_store(tmp_path, "a")
    rec = _REC.format(id="A", path=path, cand="FF0000")
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(_manifest(tmp_path, rec, rec))


@pytest.mark.parametrize("bad", ["red", "FF00", "00FF00"])  # not hex / too short / == macrophage
def test_bad_channel_colour_raises(tmp_path, bad):
    p = _manifest(tmp_path, _REC.format(id="A", path=_fake_store(tmp_path, "a"), cand=bad))
    with pytest.raises(ValueError):
        load_manifest(p)


def test_unsafe_id_raises(tmp_path):
    p = _manifest(tmp_path, _REC.format(id="../evil", path=_fake_store(tmp_path, "a"), cand="FF0000"))
    with pytest.raises(ValueError, match="filesystem-safe"):
        load_manifest(p)


def test_dims_mismatch_raises_without_real_data(tmp_path):
    path = _fake_store(tmp_path, "a", shape=(24, 3, 8, 16, 99))
    p = _manifest(tmp_path, _REC.format(id="A", path=path, cand="FF0000"))
    with pytest.raises(ValueError, match="dims.X=16 disagrees"):
        load_manifest(p)


def test_unknown_id_lists_valid_ids(tmp_path):
    p = _manifest(tmp_path, _REC.format(id="A", path=_fake_store(tmp_path, "a"), cand="FF0000"))
    with pytest.raises(ValueError, match=r"manifest lists \['A'\]"):
        get("B", p)


def test_resolve_dataset_id_defaults_to_only_dataset(tmp_path):
    from canmac.io.manifest import resolve_dataset_id

    one = _manifest(tmp_path, _REC.format(id="A", path=_fake_store(tmp_path, "a"), cand="FF0000"))
    assert resolve_dataset_id(None, one) == "A"
    two_dir = tmp_path / "two"
    two_dir.mkdir()
    two = _manifest(two_dir,
                    _REC.format(id="A", path=_fake_store(two_dir, "a"), cand="FF0000"),
                    _REC.format(id="B", path=_fake_store(two_dir, "b"), cand="FF0000"))
    with pytest.raises(ValueError, match="--dataset is required"):
        resolve_dataset_id(None, two)


def test_canmac_manifest_env_var_is_the_default(tmp_path, monkeypatch):
    p = _manifest(tmp_path, _REC.format(id="envds", path=_fake_store(tmp_path, "a"), cand="FF0000"))
    monkeypatch.setenv("CANMAC_MANIFEST", str(p))
    assert [d["id"] for d in datasets()] == ["envds"]


def test_bleach_correct_must_be_booleans(tmp_path):
    rec = _REC.format(id="A", path=_fake_store(tmp_path, "a"), cand="FF0000")
    rec += "    bleach_correct: {candida: yes-please}\n"
    with pytest.raises(ValueError, match="bleach_correct"):
        load_manifest(_manifest(tmp_path, rec))


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
