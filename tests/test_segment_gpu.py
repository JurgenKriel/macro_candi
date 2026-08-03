"""GPU-gated integration scaffolds (Wave-2 evidence; RED until Plan 01).

These run one real ROI2 timepoint through Cellpose-SAM (``cpsam``) on a GPU node
and assert non-empty, separated instances (SC1/SC2) for macrophage and non-empty
foreground (SEG-02) for candida. They are NOT run in Wave 0 — they skip without a
CUDA device (mirroring ``test_smoke.py``'s ``@pytest.mark.gpu`` + ``RUN_GPU`` gate)
and the module skips entirely until Plan 01 lands ``segment.py``.

Invoke on a GPU node with::

    RUN_GPU=1 pixi run --frozen pytest tests/test_segment_gpu.py -x
"""

from __future__ import annotations

import os
import time

import pytest

# Module-under-test guard: skip until Plan 01 creates segment.py.
seg = pytest.importorskip("canmac.stages.segment")


def _require_gpu():
    """Skip off-GPU (login node); hard-fail if RUN_GPU is set but no CUDA present."""
    import torch

    if not torch.cuda.is_available():
        if os.environ.get("RUN_GPU"):
            pytest.fail("RUN_GPU set but torch.cuda.is_available() is False")
        pytest.skip("no CUDA device (login node); GPU path is Wave-2 evidence")
    return torch


@pytest.mark.gpu
def test_macrophage_instances():
    """One crowded ROI2 macrophage timepoint yields >1 separated instance (SC1/SC2)."""
    torch = _require_gpu()

    params = {"cellprob_threshold": 0.0}
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    labels = seg.segment_timepoint("ROI2", "macrophage", 100, params)
    dt = time.time() - t0
    peak_gb = torch.cuda.max_memory_allocated() / 1e9
    print(f"macrophage t=100: n_objects={int(labels.max())} runtime={dt:.1f}s peak_vram={peak_gb:.2f}GB")

    assert labels.max() > 1  # touching macrophages separated into distinct instances


@pytest.mark.gpu
def test_candida_foreground():
    """A mid/late ROI2 candida timepoint yields non-empty foreground (SEG-02)."""
    torch = _require_gpu()

    params = {"cellprob_threshold": 0.0}
    labels = seg.segment_timepoint("ROI2", "candida", 100, params)
    print(f"candida t=100: fg_voxels={int((labels > 0).sum())}")

    assert (labels > 0).sum() > 0  # foreground present on a mid/late frame
