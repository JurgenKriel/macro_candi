"""Wave-0 smoke tests: the full stack imports, and CUDA is available on a GPU.

``test_full_stack_imports`` retires RESEARCH assumptions A1/A5 (pixi resolves the
conda-forge + PyPI-torch stack together and it all imports) and runs on the
login node. ``test_cuda_available`` is GPU-only: it is skipped unless a CUDA
device is actually present, and the on-GPU confirmation is driven by ``smoke.sh``
/ Plan 05 (``RUN_GPU=1``).
"""

from __future__ import annotations

import os

import pytest


def test_full_stack_imports():
    """Every core + later-phase library imports with no dependency conflict."""
    import torch  # noqa: F401
    import zarr  # noqa: F401
    import dask  # noqa: F401
    import ome_zarr  # noqa: F401
    import skimage  # noqa: F401
    import skan  # noqa: F401
    import napari  # noqa: F401
    import cellpose  # noqa: F401
    import networkx  # noqa: F401


@pytest.mark.gpu
def test_cuda_available():
    """CUDA must be available on a GPU node (D-10 fail-loud, T-01-00-03).

    Skipped when no GPU is present (the login node) unless ``RUN_GPU`` forces it,
    in which case a missing GPU is a hard failure. Plan 05 submits this on a GPU
    node via ``smoke.sh`` with ``RUN_GPU=1``.
    """
    import torch

    if not torch.cuda.is_available():
        if os.environ.get("RUN_GPU"):
            pytest.fail("RUN_GPU set but torch.cuda.is_available() is False")
        pytest.skip("no CUDA device (login node); GPU path verified by smoke.sh / Plan 05")
    assert torch.cuda.is_available()
