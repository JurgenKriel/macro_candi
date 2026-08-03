"""Spatial-calibration + calibration-sidecar tests (D-05, IO-02).

Spatial calibration is read from the NGFF ``multiscales`` ``scale`` and isotropy
is asserted (max pairwise scale diff < 1e-6 um); a non-isotropic voxel must fail
loud rather than silently corrupt every downstream volume measurement.

The emit tests (Task 2) assert the ``calibration.json`` sidecar is written
atomically (payload + ``.done`` sentinel) in um / um3 / (um/min) units.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from canmac.io.metadata import spatial_um
from canmac.stages.calibrate import emit_calibration

REPO_ROOT = Path(__file__).resolve().parent.parent
_VOXEL_UM = 0.14499219272808386


def _zattrs(roi: str) -> Path:
    p = REPO_ROOT / roi / "raw.zarr" / "0" / ".zattrs"
    if not p.exists():
        pytest.skip(f"data not present: {p}")
    return p


def _ome_xml(roi: str) -> Path:
    p = REPO_ROOT / roi / "raw.zarr" / "OME" / "METADATA.ome.xml"
    if not p.exists():
        pytest.skip(f"data not present: {p}")
    return p


@pytest.mark.parametrize("roi", ["ROI2", "ROI7"])
def test_voxel_reads_0145(roi: str) -> None:
    out = spatial_um(_zattrs(roi))
    assert abs(out["voxel_um"] - _VOXEL_UM) < 1e-9
    assert out["isotropic"] is True


@pytest.mark.parametrize("roi", ["ROI2", "ROI7"])
def test_voxel_um3_is_cube(roi: str) -> None:
    out = spatial_um(_zattrs(roi))
    assert abs(out["voxel_um3"] - out["voxel_um"] ** 3) < 1e-12


def _write_zattrs(tmp_path: Path, scale: list[float]) -> Path:
    zattrs = tmp_path / ".zattrs"
    zattrs.write_text(
        json.dumps(
            {
                "multiscales": [
                    {
                        "datasets": [
                            {"coordinateTransformations": [{"type": "scale", "scale": scale}]}
                        ]
                    }
                ]
            }
        )
    )
    return zattrs


def test_anisotropy_raises(tmp_path: Path) -> None:
    """A non-isotropic voxel must raise AssertionError (fail loud, V5)."""
    aniso = _write_zattrs(tmp_path, [1.0, 1.0, 0.5, 0.145, 0.145])
    with pytest.raises(AssertionError):
        spatial_um(aniso)


def test_isotropic_synthetic_passes(tmp_path: Path) -> None:
    iso = _write_zattrs(tmp_path, [1.0, 1.0, 0.145, 0.145, 0.145])
    out = spatial_um(iso)
    assert abs(out["voxel_um"] - 0.145) < 1e-9
    assert out["isotropic"] is True


# --- Task 2: calibration.json sidecar emit (IO-02) ---


@pytest.mark.parametrize("roi", ["ROI2", "ROI7"])
def test_emit_calibration_content(roi: str, tmp_path: Path) -> None:
    """emit_calibration writes voxel/timestamps/units to calibration.json."""
    out_dir = str(tmp_path / "{roi}")
    path = emit_calibration(roi, _zattrs(roi), _ome_xml(roi), out_dir=out_dir)
    rec = json.loads(Path(path).read_text())
    assert rec["dataset"] == roi
    assert abs(rec["voxel_um"] - _VOXEL_UM) < 1e-9
    assert abs(rec["voxel_um3"] - _VOXEL_UM**3) < 1e-12
    assert rec["isotropic"] is True
    assert rec["n_timepoints"] == 120
    assert len(rec["timestamps_s"]) == 120
    assert abs(rec["interval_min_mean"] * 60.0 - 180.04) < 0.5


@pytest.mark.parametrize("roi", ["ROI2", "ROI7"])
def test_emit_calibration_units_and_sentinel(roi: str, tmp_path: Path) -> None:
    """Units block is um/um3/(um/min) and a .done sentinel is written last."""
    out_dir = str(tmp_path / "{roi}")
    path = emit_calibration(roi, _zattrs(roi), _ome_xml(roi), out_dir=out_dir)
    rec = json.loads(Path(path).read_text())
    assert rec["units"] == {"length": "um", "volume": "um3", "rate": "um/min"}
    assert Path(path + ".done").exists()


def test_emit_calibration_default_out_dir_template(roi_tmp: Path) -> None:
    """The {roi} placeholder in out_dir is filled with the dataset id."""
    path = emit_calibration(
        "ROI2", _zattrs("ROI2"), _ome_xml("ROI2"), out_dir=str(roi_tmp / "cal" / "{roi}")
    )
    assert path.endswith("ROI2/calibration.json")
    assert Path(path).exists()


@pytest.fixture
def roi_tmp(tmp_path: Path) -> Path:
    return tmp_path
