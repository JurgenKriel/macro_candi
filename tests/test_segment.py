"""CPU unit tests for the Phase-3 segmentation stage (RED until Plan 01).

These import ``canmac.stages.segment`` via ``pytest.importorskip`` so pytest
COLLECTION always succeeds; the whole module skips until Plan 01 lands
``segment.py``, then it flips GREEN. No GPU and no on-disk store are needed — the
GPU ``eval`` is replaced by a recording fake model, and the reader ``get_view`` is
monkeypatched to yield a tiny synthetic lazy volume.

Covers (RESEARCH "Phase Requirements -> Test Map"):
- ``test_requires_gpu``      — fail-loud GPU guard (D-03)
- ``test_eval_config``       — load-bearing eval kwargs (D-02): do_3D/z_axis/anisotropy
- ``test_input_cast_bigendian`` — >u2 -> native float32 C-contiguous (Pitfall 2)
- ``test_atomic_label_store`` — uint16 label Zarr round-trip + .done + restart skip (D-06)
- ``test_census_schema_and_plausibility`` — census schema + not-flat/not-all-zero (SEG-04, SC3)
"""

from __future__ import annotations

import numpy as np
import pytest

# Module-under-test guard: skip the whole file until Plan 01 creates segment.py.
seg = pytest.importorskip("canmac.stages.segment")


class FakeModel:
    """A stand-in for cellpose ``CellposeModel`` that records the eval call.

    ``eval`` returns ``(labels, flows, styles)`` like the real API, capturing the
    exact volume and kwargs the stage passed so the config can be asserted on CPU.
    """

    def __init__(self):
        self.eval_kwargs = None
        self.eval_vol = None

    def eval(self, vol, **kwargs):
        self.eval_vol = vol
        self.eval_kwargs = kwargs
        return np.zeros(vol.shape, dtype=np.uint16), None, None


def _fake_get_view_factory(vol):
    """Return a ``get_view``-compatible stub yielding ``vol`` as a lazy dask array."""
    import dask.array as da

    def _fake_get_view(dataset, channel, t, correct=False):
        return da.from_array(vol, chunks=(1, *vol.shape[1:]))

    return _fake_get_view


def test_requires_gpu(monkeypatch):
    """The model loader fails loud when no CUDA device is present (D-03)."""
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises((AssertionError, RuntimeError)):
        seg._load_model()


def test_eval_config(monkeypatch):
    """Load-bearing eval kwargs are passed: native-3D isotropic, diameter-invariant."""
    fake = FakeModel()
    vol = np.zeros((4, 8, 8), dtype=">u2")
    monkeypatch.setattr(seg, "get_view", _fake_get_view_factory(vol))

    params = {"cellprob_threshold": 0.0}
    seg.segment_timepoint("ROI2", "macrophage", 0, params, model=fake)

    kw = fake.eval_kwargs
    # The stage must call model.eval(vol, do_3D=True, z_axis=0, anisotropy=1.0, ...).
    assert kw["do_3D"] is True  # native-3D path (do_3D=True; D-02), not 2D-stitch
    assert kw["z_axis"] == 0  # REQUIRED: (Z,Y,X) grayscale (ROI2 Y=401 > Z=301)
    assert kw["channel_axis"] is None  # grayscale; cellpose replicates internally
    assert kw["anisotropy"] == 1.0  # isotropic 0.145 um (D-02)
    # Cellpose 4 is diameter-invariant — diameter must NOT be pinned to a number.
    assert kw.get("diameter") is None


def test_input_cast_bigendian(monkeypatch):
    """A big-endian >u2 volume reaches the model as native-order float32, C-contiguous."""
    fake = FakeModel()
    vol = np.zeros((4, 8, 8), dtype=">u2")
    vol[:2, :2, :2] = 1000
    monkeypatch.setattr(seg, "get_view", _fake_get_view_factory(vol))

    seg.segment_timepoint("ROI2", "macrophage", 0, {"cellprob_threshold": 0.0}, model=fake)

    arr = fake.eval_vol
    assert arr.dtype == np.float32
    # Native byte order is "=" or, for single-byte-insensitive, "|".
    assert arr.dtype.byteorder in ("=", "|")
    assert arr.flags["C_CONTIGUOUS"]


def test_atomic_label_store(synthetic_labels, tmp_results):
    """Labels round-trip uint16 through the label Zarr; .done written last; restart skips."""
    import zarr

    from canmac.io.atomic import is_done

    store_path = str(tmp_results / "macrophage_labels.zarr")
    seg.write_labels(store_path, t=5, labels=synthetic_labels)

    root = zarr.open_group(store_path, mode="r")
    stored = root["t005"][:]
    assert stored.dtype == np.uint16
    assert np.array_equal(stored, synthetic_labels)
    assert is_done(f"{store_path}/t005")  # per-timepoint sentinel present (LAST)

    # A second write for the same t is a no-op (restart skip, D-08): payload unchanged.
    seg.write_labels(store_path, t=5, labels=np.zeros_like(synthetic_labels))
    reopened = zarr.open_group(store_path, mode="r")
    assert np.array_equal(reopened["t005"][:], synthetic_labels)


def test_census_schema_and_plausibility():
    """census_row has the fixed schema; counts vary and are not all-zero (SC3)."""
    rows = []
    for t in range(5):
        labels = np.zeros((4, 8, 8), dtype=np.uint16)
        n = t + 1  # varying object count 1..5 across the sequence
        for i in range(n):
            labels.flat[i * 5] = i + 1  # place n distinct instance IDs
        row = seg.census_row("ROI2", "macrophage", t, labels)
        assert set(row.keys()) == {"dataset", "channel", "t", "n_objects", "fg_voxels"}
        assert row["n_objects"] == int(labels.max())  # cellpose IDs contiguous 1..N
        rows.append(row)

    counts = [r["n_objects"] for r in rows]
    assert np.std(counts) > 0  # not suspiciously flat
    assert any(c > 0 for c in counts)  # not all-zero


def test_main_rejects_bad_t(monkeypatch, capsys):
    """main() fail-loud rejects an out-of-range --t (V5 input validation, T-03-01-04).

    Bounds are checked against the manifest dims BEFORE any GPU/params work, so a
    bad SLURM_ARRAY_TASK_ID exits non-zero instead of silently segmenting nothing.
    """
    # Manifest stub: ROI2 with T=120 so --t 999 is out of range.
    monkeypatch.setattr(
        "canmac.io.manifest.get",
        lambda dataset_id, path=None: {"id": dataset_id, "dims": {"T": 120}},
    )
    monkeypatch.setattr(
        "sys.argv",
        ["segment", "--dataset", "ROI2", "--channel", "macrophage", "--t", "999"],
    )
    with pytest.raises(SystemExit) as exc:
        seg.main()
    assert exc.value.code != 0  # argparse.error exits 2
    err = capsys.readouterr().err
    assert "out of range" in err


def test_preprocess_volume_suppresses_halo():
    """tophat + dog reduce a synthetic diffuse halo while keeping the compact core; none=identity."""
    from scipy.ndimage import gaussian_filter

    core = np.zeros((24, 48, 48), np.float32)
    core[10:14, 22:26, 22:26] = 100.0                       # compact bright core
    halo = gaussian_filter((core > 0).astype(np.float32), sigma=6) * 50.0  # broad glow
    vol = core + halo
    hz, hy, hx = 12, 22, 12                                  # a halo-only voxel (off-core)
    base = float(vol[hz, hy, hx])
    assert base > 0
    assert np.array_equal(seg.preprocess_volume(vol, "none"), vol)          # identity
    th = seg.preprocess_volume(vol, "tophat", tophat_radius=6)
    dg = seg.preprocess_volume(vol, "dog", dog_low=1.0, dog_high=6.0)
    assert th[hz, hy, hx] < base and dg[hz, hy, hx] < base                  # halo suppressed
    assert th[12, 24, 24] > th[hz, hy, hx]                                  # core retained
    import pytest as _pt
    with _pt.raises(ValueError):
        seg.preprocess_volume(vol, "bogus")


def test_normalize_volume_equalizes_dim_and_bright():
    """localstd/clahe bring a DIM copy of a structure closer to a BRIGHT one; none=identity."""
    from scipy.ndimage import gaussian_filter

    v = np.zeros((24, 64, 64), np.float32)
    v[10:14, 10:20, 8:56] = 1000.0                  # bright segment
    v[10:14, 40:50, 8:56] = 120.0                   # SAME structure type but DIM
    v = gaussian_filter(v, 1.0) + 5.0
    assert np.array_equal(seg.normalize_volume(v, "none"), v)
    raw_ratio = v[12, 15, 30] / v[12, 45, 30]        # bright/dim contrast before
    for m, kw in (("localstd", {"sigma": 6.0, "mask_percentile": 90.0}), ("clahe", {})):
        out = seg.normalize_volume(v, m, **kw)
        b, d = float(out[12, 15, 30]), float(out[12, 45, 30])
        assert b > out.min() and d > out.min()       # both segments survive
        new_ratio = (b - out.min()) / max(d - out.min(), 1e-6)
        assert new_ratio < raw_ratio                 # dim brought closer to bright
    import pytest as _pt
    with _pt.raises(ValueError):
        seg.normalize_volume(v, "bogus")
