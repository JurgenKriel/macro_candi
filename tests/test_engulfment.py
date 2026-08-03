"""CPU tests for the Phase-5 containment prototype (canmac.stages.engulfment).

Synthetic geometry: a macrophage cube with (a) a candida fully inside, (b) one straddling the
boundary (half in), (c) one far outside. Asserts the overlap fractions and PROPOSED classes.
"""

from __future__ import annotations

import numpy as np
import pytest

eng = pytest.importorskip("canmac.stages.engulfment")


def _scene():
    mac = np.zeros((40, 60, 60), np.uint16)
    mac[10:30, 10:30, 10:30] = 1            # macrophage 1
    mac[10:30, 40:55, 40:55] = 2            # macrophage 2
    cand = np.zeros((40, 60, 60), np.uint16)
    cand[15:20, 15:20, 15:20] = 1           # fully INSIDE macrophage 1
    cand[15:20, 26:34, 15:20] = 2           # straddles mac-1 boundary (~half in)
    cand[32:36, 5:9, 5:9] = 3               # FREE (outside both)
    return mac, cand


def test_containment_fractions_and_classes():
    mac, cand = _scene()
    pairs, per_c = eng.containment(mac, cand)
    by = {int(r.candida): r for r in per_c.itertuples()}
    # (a) fully inside -> frac 1.0, engulfed, matched to macrophage 1
    assert by[1].overlap_frac == pytest.approx(1.0)
    assert by[1].PROPOSED_class == "engulfed"
    assert by[1].best_macrophage == 1
    # (b) straddling -> strictly between, classed partial
    assert 0.05 < by[2].overlap_frac < 0.9
    assert by[2].PROPOSED_class == "partial"
    # (c) outside -> zero overlap, free, no macrophage
    assert by[3].overlap_frac == pytest.approx(0.0)
    assert by[3].PROPOSED_class == "free"
    assert by[3].best_macrophage == 0
    # pairs table only contains real (candida, macrophage) overlaps
    assert set(pairs["macrophage"]) <= {1, 2}
    assert (pairs["overlap_frac"] <= 1.0).all()


def test_thresholds_are_tunable():
    mac, cand = _scene()
    # a very low engulf_frac promotes the straddling object to engulfed
    _p, per_c = eng.containment(mac, cand, engulf_frac=0.1)
    by = {int(r.candida): r.PROPOSED_class for r in per_c.itertuples()}
    assert by[2] == "engulfed"
    # a high touch_frac demotes it to free
    _p, per_c = eng.containment(mac, cand, touch_frac=0.95)
    by = {int(r.candida): r.PROPOSED_class for r in per_c.itertuples()}
    assert by[2] == "free"


def test_empty_candida_is_safe():
    mac, _ = _scene()
    pairs, per_c = eng.containment(mac, np.zeros_like(mac))
    assert len(pairs) == 0 and len(per_c) == 0          # no rows, no crash
    assert list(per_c.columns)[:2] == ["candida", "candida_vox"]   # columns preserved
