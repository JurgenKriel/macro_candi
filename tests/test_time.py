"""Time-calibration tests (D-06, IO-02).

The frame interval must come from the OME ``Plane/@DeltaT`` per-timepoint
timestamps in ``raw.zarr/OME/METADATA.ome.xml`` — NEVER the NGFF ``t`` scale
(which is 1.0). These tests assert the authoritative-source parse for BOTH ROIs:
exactly 120 monotonic timestamps with a mean interval of ~180.04 s (3.001 min).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from canmac.io.metadata import timestamps_s

REPO_ROOT = Path(__file__).resolve().parent.parent


def _ome_xml(roi: str) -> Path:
    p = REPO_ROOT / roi / "raw.zarr" / "OME" / "METADATA.ome.xml"
    if not p.exists():
        pytest.skip(f"data not present: {p}")
    return p


@pytest.mark.parametrize("roi", ["ROI2", "ROI7"])
def test_120_timestamps(roi: str) -> None:
    """Exactly 120 per-timepoint DeltaT timestamps parse for both ROIs."""
    out = timestamps_s(_ome_xml(roi))
    assert out["n"] == 120
    assert len(out["timestamps_s"]) == 120


@pytest.mark.parametrize("roi", ["ROI2", "ROI7"])
def test_timestamps_monotonic(roi: str) -> None:
    """Timestamps are strictly monotonic increasing (fail loud on disorder)."""
    ts = timestamps_s(_ome_xml(roi))["timestamps_s"]
    assert all(ts[i + 1] > ts[i] for i in range(len(ts) - 1))


@pytest.mark.parametrize("roi", ["ROI2", "ROI7"])
def test_mean_interval_seconds(roi: str) -> None:
    """Mean frame interval is within 180.04 +/- 0.5 s (3.001 min)."""
    out = timestamps_s(_ome_xml(roi))
    mean_s = out["interval_min_mean"] * 60.0
    assert abs(mean_s - 180.04) < 0.5, f"{roi}: mean interval {mean_s} s"


@pytest.mark.parametrize("roi", ["ROI2", "ROI7"])
def test_interval_min_length_and_mean(roi: str) -> None:
    """interval_min has length 119 (n-1) and mean ~3.001 min."""
    out = timestamps_s(_ome_xml(roi))
    assert len(out["interval_min"]) == 119
    assert abs(out["interval_min_mean"] - 3.001) < 0.02
