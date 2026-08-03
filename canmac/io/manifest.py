"""Dataset manifest loader with fail-loud validation (D-01/D-02, IO-04).

``manifest.yaml`` hand-enumerates EXACTLY the 2 timelapses (ROI2, ROI7). This
loader parses it with ``yaml.safe_load`` and validates every invariant against
the on-disk stores, raising loudly on any drift (RESEARCH Security V5):

  * exactly 2 datasets with ids {ROI2, ROI7} (a count of 50 — the corrected
    `tile_manifest.json` bug — or any other count raises)
  * each ``zarr_path`` exists and contains ``<zarr_path>/<component>/.zarray``
  * per-dataset XY dims cross-checked against the actual ``.zarray`` shape
    (ROI2 Y=401,X=369 ; ROI7 Y=291,X=475)
  * ``parallel_unit == "timepoint"`` and ``T == 120`` (D-02)
  * ``channels.discard`` contains FF00FF; candida/macrophage are FF0000/00FF00

A validated manifest is the single source of dataset identity every later phase
reads through, so validation happens at load time — never trust a stale field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

import yaml

_EXPECTED_IDS = {"ROI2", "ROI7"}
_EXPECTED_T = 120
_EXPECTED_CANDIDA = "FF0000"
_EXPECTED_MACROPHAGE = "00FF00"
_EXPECTED_DISCARD = "FF00FF"

# Default manifest location = repo root (parent of canmac/).
_DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "manifest.yaml"


def _zarray_shape(store: Path, component: str) -> tuple[int, ...]:
    zarray = store / component / ".zarray"
    if not zarray.exists():
        raise ValueError(f"missing .zarray: {zarray}")
    with open(zarray) as f:
        return tuple(json.load(f)["shape"])


def _validate(record: dict, base: Path) -> dict:
    if not isinstance(record, dict) or "datasets" not in record:
        raise ValueError("manifest must be a mapping with a 'datasets' key")
    datasets = record["datasets"]
    if len(datasets) != 2:
        raise ValueError(f"expected exactly 2 datasets, got {len(datasets)}")
    ids = {d.get("id") for d in datasets}
    if ids != _EXPECTED_IDS:
        raise ValueError(f"expected dataset ids {_EXPECTED_IDS}, got {ids}")

    for d in datasets:
        did = d["id"]
        # Channels: colors recorded by omero hex (D-03).
        ch = d.get("channels", {})
        if ch.get("candida") != _EXPECTED_CANDIDA:
            raise ValueError(f"{did}: candida must be {_EXPECTED_CANDIDA}, got {ch.get('candida')}")
        if ch.get("macrophage") != _EXPECTED_MACROPHAGE:
            raise ValueError(
                f"{did}: macrophage must be {_EXPECTED_MACROPHAGE}, got {ch.get('macrophage')}"
            )
        if _EXPECTED_DISCARD not in (ch.get("discard") or []):
            raise ValueError(f"{did}: channels.discard must contain {_EXPECTED_DISCARD}")

        # Parallelism unit is the timepoint (D-02), not the scene.
        if d.get("parallel_unit") != "timepoint":
            raise ValueError(f"{did}: parallel_unit must be 'timepoint', got {d.get('parallel_unit')}")

        dims = d.get("dims", {})
        if dims.get("T") != _EXPECTED_T:
            raise ValueError(f"{did}: T must be {_EXPECTED_T}, got {dims.get('T')}")

        # Cross-check declared dims against the on-disk .zarray shape (T,C,Z,Y,X).
        store = (base / d["zarr_path"]).resolve()
        if not store.exists():
            raise ValueError(f"{did}: zarr_path does not exist: {store}")
        shape = _zarray_shape(store, d["component"])
        t, c, z, y, x = shape
        for name, declared, actual in (
            ("T", dims.get("T"), t),
            ("C", dims.get("C"), c),
            ("Z", dims.get("Z"), z),
            ("Y", dims.get("Y"), y),
            ("X", dims.get("X"), x),
        ):
            if declared != actual:
                raise ValueError(
                    f"{did}: dims.{name}={declared} disagrees with on-disk .zarray {name}={actual} "
                    f"(shape {shape})"
                )
        # Resolve the absolute store path for downstream consumers.
        d["_store"] = str(store)

    return record


def load_manifest(path: Union[str, Path, None] = None) -> dict:
    """Load and validate the dataset manifest; return the parsed record.

    Paths inside the manifest are resolved relative to the manifest file's
    directory. Raises ``ValueError`` on any invariant drift.
    """
    manifest_path = Path(path) if path is not None else _DEFAULT_MANIFEST
    with open(manifest_path) as f:
        record = yaml.safe_load(f)
    return _validate(record, base=manifest_path.resolve().parent)


def datasets(path: Union[str, Path, None] = None) -> list[dict]:
    """Return the validated list of dataset records."""
    return load_manifest(path)["datasets"]


def get(dataset_id: str, path: Union[str, Path, None] = None) -> dict:
    """Return the validated record for ``dataset_id`` (e.g. "ROI2")."""
    for d in datasets(path):
        if d["id"] == dataset_id:
            return d
    raise ValueError(f"unknown dataset id {dataset_id!r}")
