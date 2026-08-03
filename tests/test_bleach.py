"""Photobleaching-correction tests (IO-03, D-07/D-08).

Task 1 (synthetic, fast — no on-disk data needed): the foreground mean masks
before averaging (never whole-volume), the reference factor is 1.0, and applying
the factors flattens a decaying foreground-mean series so the corrected linear-fit
slope magnitude is < 25% of the raw slope (RESEARCH "Thresholds").

Task 2 (integration — skip when the ingest artifacts / raw data are absent):
``bleach.json`` records method + ref + 120 factors/channel, the QC decay PNGs
exist, and ``get_view(..., correct=True)`` applies the stored factor as a LAZY
dask multiply (differs from ``correct=False`` by exactly the stored factor).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import dask.array as da
import numpy as np
import pytest

from canmac.io.bleach import (
    compute_bleach_factors,
    factors_from_series,
    foreground_mean,
    linear_slope,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- Task 1: foreground factor computation (synthetic, fast) -----------------


def test_foreground_mean_masks_before_averaging():
    """foreground_mean uses a threshold mask — it is NOT the whole-volume mean.

    A volume that is mostly dim background with a small bright blob must report a
    foreground mean far above the whole-volume mean (the anti-pattern the plan
    forbids: background dominates the whole-volume mean as signal decays).
    """
    vol = np.full((20, 20, 20), 5.0, dtype=np.float32)  # dim background
    vol[:3, :3, :3] = 200.0  # small bright foreground blob
    whole = float(vol.mean())
    fg = foreground_mean(vol)
    assert fg > whole * 5  # foreground is the bright blob, not the dim mean
    assert fg == pytest.approx(200.0, rel=1e-6)


def test_foreground_mean_handles_bigendian_uint16():
    """A big-endian >u2 volume (the on-disk store dtype) computes on native floats."""
    vol = np.zeros((10, 10, 10), dtype=">u2")
    vol[:2, :2, :2] = 1000
    fg = foreground_mean(vol)
    assert math.isfinite(fg)
    assert fg == pytest.approx(1000.0, rel=1e-6)


def test_reference_factor_is_one():
    """f[ref] == 1.0 (the reference frame is unchanged by correction)."""
    S = [100.0, 80.0, 64.0, 51.2]
    for ref in (0, 2):
        factors = factors_from_series(S, ref=ref)
        assert factors[ref] == pytest.approx(1.0)


def test_factor_is_ratio_to_reference():
    """f[t] == S[ref]/S[t] (multiplicative correction toward the reference)."""
    S = [100.0, 50.0, 25.0]
    factors = factors_from_series(S, ref=0)
    assert factors[1] == pytest.approx(2.0)
    assert factors[2] == pytest.approx(4.0)


def test_correction_flattens_decay_slope():
    """Corrected foreground-mean slope magnitude < 25% of the raw slope.

    Decaying series S[t] = A*exp(-t/tau) + C. Ratio-to-reference makes the
    corrected series flat; its residual slope is far below the 25% threshold.
    """
    t = np.arange(120)
    S = 500.0 * np.exp(-t / 40.0) + 50.0
    factors = factors_from_series(list(S), ref=0)
    corrected = np.array([S[i] * factors[i] for i in range(len(S))])
    raw_slope = abs(linear_slope(S, t))
    corr_slope = abs(linear_slope(corrected, t))
    assert raw_slope > 0  # the raw series really does decay
    assert corr_slope < 0.25 * raw_slope


def test_compute_bleach_factors_with_injected_volume_source():
    """compute_bleach_factors streams one volume per t and returns 120 factors.

    An injected volume_source feeds synthetic decaying volumes (no disk read), so
    the streaming/aggregation contract is tested without the ~32 GB store.
    """
    rng = np.random.default_rng(0)

    def volume_source(t: int):
        amp = 500.0 * math.exp(-t / 40.0) + 50.0
        vol = np.full((16, 16, 16), 2.0, dtype=np.float32)  # dim background
        vol[:4, :4, :4] = amp  # bright foreground scales with decay
        return vol

    res = compute_bleach_factors("ROI2", "candida", n_t=120, volume_source=volume_source)
    assert res["method"] == "ratio_foreground_mean"
    assert res["ref"] == 0
    assert len(res["factors"]) == 120
    assert res["factors"][0] == pytest.approx(1.0)
    S = [res["S_raw"][t] for t in range(120)]
    corrected = [S[t] * res["factors"][t] for t in range(120)]
    raw_slope = abs(linear_slope(S))
    corr_slope = abs(linear_slope(corrected))
    assert corr_slope < 0.25 * raw_slope


# --- Task 2: lazy apply in get_view + ingest artifacts (integration) ---------


def _bleach_json(dataset: str) -> Path:
    return REPO_ROOT / "results" / dataset / "bleach.json"


def test_bleach_json_records_method_ref_and_120_factors():
    """results/ROI2/bleach.json records method + ref + 120 factors per channel."""
    path = _bleach_json("ROI2")
    if not path.exists():
        pytest.skip(f"ingest artifact not present: {path}")
    payload = json.loads(path.read_text())
    for channel in ("candida", "macrophage"):
        ch = payload["channels"][channel]
        assert "method" in ch and "ref" in ch
        assert len(ch["factors"]) == 120


def test_qc_decay_plots_exist():
    """The raw-vs-corrected QC decay PNGs exist for ROI2 both channels."""
    qc = REPO_ROOT / "results" / "ROI2" / "qc"
    for channel in ("candida", "macrophage"):
        png = qc / f"bleach_decay_{channel}.png"
        if not png.exists():
            pytest.skip(f"QC plot not present: {png}")
        assert png.stat().st_size > 0


def test_get_view_macrophage_correct_is_lazy_and_differs_by_stored_factor():
    """MACROPHAGE get_view(correct=True) stays lazy and differs from raw by f[t].

    Macrophage is the APPLIED channel (01-04 policy "correct macrophage only"):
    the correction multiplies the raw view by the stored bleach factor.
    """
    from canmac.io.bleach import factor_for

    store = REPO_ROOT / "ROI2" / "raw.zarr"
    if not store.exists() or not _bleach_json("ROI2").exists():
        pytest.skip("raw data or bleach.json not present")

    from canmac.io.reader import get_view

    t = 119
    raw = get_view("ROI2", "macrophage", t=t, correct=False)
    corr = get_view("ROI2", "macrophage", t=t, correct=True)
    assert isinstance(corr, da.Array)  # still lazy — not materialized
    assert hasattr(corr, "compute")

    f = factor_for("ROI2", "macrophage", t)
    assert f != pytest.approx(1.0)  # a real (non-identity) photobleaching factor
    raw_v = np.asarray(raw.compute(), dtype=np.float64)
    corr_v = np.asarray(corr.compute(), dtype=np.float64)
    assert np.allclose(corr_v, raw_v * f, rtol=1e-5, atol=1e-4)


def test_get_view_candida_correct_is_noop_returns_raw():
    """CANDIDA get_view(correct=True) is a NO-OP returning the RAW values.

    Candida's foreground rises (biological growth), so its factor is stored for
    provenance but flagged apply=false; the reader must NOT divide the growth
    signal away — correct=True returns pixel-identical raw values (01-04 decision).
    """
    store = REPO_ROOT / "ROI2" / "raw.zarr"
    if not store.exists() or not _bleach_json("ROI2").exists():
        pytest.skip("raw data or bleach.json not present")

    from canmac.io.bleach import applies_on_read
    from canmac.io.reader import get_view

    assert applies_on_read("ROI2", "candida") is False   # policy recorded in bleach.json
    t = 119
    raw = get_view("ROI2", "candida", t=t, correct=False)
    corr = get_view("ROI2", "candida", t=t, correct=True)
    assert isinstance(corr, da.Array)  # still a lazy dask array
    raw_v = np.asarray(raw.compute(), dtype=np.float64)
    corr_v = np.asarray(corr.compute(), dtype=np.float64)
    assert np.array_equal(corr_v, raw_v)  # identical — no factor applied


def test_bleach_json_records_apply_policy():
    """bleach.json records apply=true for macrophage and apply=false for candida."""
    path = _bleach_json("ROI2")
    if not path.exists():
        pytest.skip(f"ingest artifact not present: {path}")
    payload = json.loads(path.read_text())
    assert payload["channels"]["macrophage"]["apply"] is True
    assert payload["channels"]["candida"]["apply"] is False
    assert payload["channels"]["candida"]["reason"]  # a non-empty rationale string


def test_get_view_full_stack_correct_is_lazy():
    """correct=True on the full (T,Z,Y,X) stack broadcasts the per-t factor, lazily."""
    if not _bleach_json("ROI2").exists() or not (REPO_ROOT / "ROI2" / "raw.zarr").exists():
        pytest.skip("raw data or bleach.json not present")
    from canmac.io.reader import get_view

    view = get_view("ROI2", "candida", correct=True)
    assert isinstance(view, da.Array)
    assert view.shape == (120, 301, 401, 369)
