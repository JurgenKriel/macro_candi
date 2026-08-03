"""Weights-wiring guarantee (D-04, T-03-00-01): offline cellpose finds local weights.

``CELLPOSE_LOCAL_MODELS_PATH`` must point at the PARENT dir of the pre-staged
``cpsam`` file (cellpose joins ``MODEL_DIR / "cpsam"``). If it instead points at the
old non-existent ``/vast/projects/BCRL_Multi_Omics/models/cellpose``, cellpose tries
a runtime download on the internet-less GPU node and hangs the job. This test asserts
the env var resolves to the on-disk staged weight so no download branch can fire.

It runs on the login/CPU node under ``pixi run`` (which applies ``activation.env``).
When the scratch stage is absent (e.g. CI) it skips, mirroring ``conftest._require``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

EXPECTED_MODEL_DIR = "/vast/scratch/users/kriel.j/cellpose_models"


def test_weights_path_is_parent_dir():
    """CELLPOSE_LOCAL_MODELS_PATH resolves to the staged cpsam file, no download."""
    val = os.environ.get("CELLPOSE_LOCAL_MODELS_PATH")
    if val is None:
        pytest.skip(
            "CELLPOSE_LOCAL_MODELS_PATH not set (pixi activation.env not active); "
            "run via `pixi run --frozen pytest`"
        )
    # The var must be the PARENT dir of the staged weight, never the old broken path.
    assert val == EXPECTED_MODEL_DIR, (
        f"CELLPOSE_LOCAL_MODELS_PATH={val!r} — expected {EXPECTED_MODEL_DIR!r} "
        "(the parent dir of the staged cpsam weight; D-04)"
    )
    weight = Path(val) / "cpsam"
    if not weight.exists():
        pytest.skip(f"staged weights absent: {weight}")
    assert weight.is_file(), f"expected a staged weight FILE at {weight}"
