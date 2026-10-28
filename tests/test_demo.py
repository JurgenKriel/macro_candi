"""Synthetic demo dataset — real-data-free tests of the I/O layer and the CPU stages.

The reference ROI2/ROI7 stores are not public, so the data-dependent tests elsewhere skip
in CI. These tests generate the small synthetic timelapse once (``canmac.demo``) and check
the pipeline against its known answer: channel-by-colour resolution with the decoy lysed
channel stored FIRST, calibration from the OME XML, classical 3D segmentation counts, and
engulfment classes. The full end-to-end run is ``scripts/demo/run_demo.sh``.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from canmac import demo


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    demo.generate(out)
    return out


@pytest.fixture
def demo_env(demo_dir, monkeypatch):
    """Point every manifest lookup (incl. inside get_view) at the demo manifest."""
    monkeypatch.setenv("CANMAC_MANIFEST", str(demo_dir / "manifest.yaml"))
    return demo_dir


def test_manifest_validates_against_store(demo_env):
    from canmac.io.manifest import get

    rec = get(demo.DATASET_ID)
    assert rec["dims"] == {"T": demo.N_T, "C": 3, "Z": 48, "Y": 128, "X": 128}
    assert rec["bleach_correct"] == {"macrophage": True, "candida": False}


def test_channels_resolve_by_colour_not_index(demo_env):
    from canmac.io.channels import discard_indices, resolve_channel
    from canmac.io.manifest import get

    rec = get(demo.DATASET_ID)
    zattrs = demo_env / "DEMO" / "raw.zarr" / "0" / ".zattrs"
    assert resolve_channel(zattrs, "candida", rec["channels"]) == 1
    assert resolve_channel(zattrs, "macrophage", rec["channels"]) == 2
    assert discard_indices(zattrs, rec["channels"]["discard"]) == [0]  # decoy stored first


def test_get_view_streams_one_bigendian_volume(demo_env):
    from canmac.io.reader import get_view, voxel_um

    view = get_view(demo.DATASET_ID, "macrophage", t=0)
    assert view.shape == demo.SHAPE_ZYX
    assert view.dtype == np.dtype(">u2")
    assert voxel_um(demo.DATASET_ID) == pytest.approx(demo.VOXEL_UM)


def test_calibration_reads_real_timestamps(demo_env, tmp_path):
    from canmac.io.reader import ome_xml_path
    from canmac.stages.calibrate import emit_calibration

    store = demo_env / "DEMO" / "raw.zarr"
    path = emit_calibration(demo.DATASET_ID, store / "0" / ".zattrs",
                            ome_xml_path(demo.DATASET_ID), out_dir=str(tmp_path / "{roi}"),
                            expected_n=demo.N_T)
    cal = json.loads(open(path).read())
    assert cal["voxel_um"] == pytest.approx(demo.VOXEL_UM)
    assert cal["n_timepoints"] == demo.N_T
    assert cal["timestamps_s"] == demo.timestamps_s()  # jittered, not an assumed interval
    assert cal["interval_min_mean"] == pytest.approx(3.0, abs=0.05)


def test_timestamp_count_mismatch_fails_loud(demo_env):
    from canmac.io.metadata import timestamps_s
    from canmac.io.reader import ome_xml_path

    with pytest.raises(AssertionError, match="expected 120 timestamps"):
        timestamps_s(ome_xml_path(demo.DATASET_ID), expected_n=120)


@pytest.fixture
def labels_t0(demo_env):
    from canmac.stages.segment import segment_timepoint

    def seg(channel):
        params = json.loads(open(f"params/demo/classical_{channel}.json").read())
        return segment_timepoint(demo.DATASET_ID, channel, 0, params, correct=False)

    return seg("macrophage"), seg("candida")


def test_classical_segmentation_counts(labels_t0):
    mac, cand = labels_t0
    assert mac.dtype == np.uint16
    assert int(mac.max()) == len(demo.MACROPHAGES)   # decoy lysed channel never read
    assert int(cand.max()) == 5                      # 3 yeast + 2 hyphae


def _truth(demo_dir):
    return json.loads((demo_dir / "ground_truth.json").read_text())


def test_engulfment_classes_match_ground_truth(labels_t0, demo_dir):
    from canmac.stages.engulfment import containment

    mac, cand = labels_t0
    _pairs, per_c = containment(mac, cand)
    truth = _truth(demo_dir)["frames"][0]["objects"]
    for name, centre in demo.yeast_centres(0).items():
        lab = int(cand[tuple(int(round(v)) for v in centre)])
        got = per_c.loc[per_c["candida"] == lab, "PROPOSED_class"].iloc[0]
        assert got == truth[name]["class"], name


def test_ground_truth_covers_all_engulfment_classes(demo_dir):
    y2 = {f["objects"]["Y2"]["class"] for f in _truth(demo_dir)["frames"]}
    assert y2 == {"free", "partial", "engulfed"}


def test_unknown_segmentation_method_raises(demo_env):
    from canmac.stages.segment import segment_timepoint

    with pytest.raises(ValueError, match="unknown segmentation method"):
        segment_timepoint(demo.DATASET_ID, "candida", 0, {"method": "magic"}, correct=False)
