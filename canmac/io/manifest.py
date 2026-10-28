"""Dataset manifest loader with fail-loud validation (D-01/D-02, IO-04).

``manifest.yaml`` enumerates the timelapses to analyse. This loader parses it with
``yaml.safe_load`` and validates every record against its on-disk store, raising
loudly on any drift (RESEARCH Security V5):

  * at least one dataset; ids unique and filesystem-safe (they become
    ``results/{id}/`` directories)
  * each ``zarr_path`` exists and contains ``<zarr_path>/<component>/.zarray``
  * declared ``dims`` (T, C, Z, Y, X) cross-checked against the actual ``.zarray``
    shape — a typo can never silently shift every measurement
  * ``parallel_unit == "timepoint"`` (D-02)
  * ``channels.candida`` / ``channels.macrophage`` are distinct 6-digit hex omero
    colours; ``channels.discard`` (optional) lists colours that are never read
  * ``bleach_correct`` (optional) maps channel -> bool, overriding the default
    photobleaching policy in :mod:`canmac.io.bleach`

Nothing about a particular experiment (dataset names, T, colours) is hard-coded:
the manifest is the single source of dataset identity every later stage reads
through, so validation happens at load time — never trust a stale field.

The manifest path is, in order: an explicit ``path`` argument, the
``CANMAC_MANIFEST`` environment variable, then ``manifest.yaml`` at the repo root.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional, Union

import yaml

# Repo-root fallback (parent of canmac/); overridden by $CANMAC_MANIFEST.
_REPO_MANIFEST = Path(__file__).resolve().parents[2] / "manifest.yaml"

_ROLES = ("candida", "macrophage")
_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DIM_KEYS = ("T", "C", "Z", "Y", "X")

PathLike = Union[str, Path]


def default_manifest_path() -> Path:
    """``$CANMAC_MANIFEST`` if set, else the repo-root ``manifest.yaml`` (read at call time)."""
    env = os.environ.get("CANMAC_MANIFEST")
    return Path(env) if env else _REPO_MANIFEST


def use_manifest(path: Optional[PathLike]) -> None:
    """Make ``path`` the process-wide manifest (sets ``CANMAC_MANIFEST``); ``None`` is a no-op.

    CLIs call this with their ``--manifest`` value so every later lookup — including
    ones deep inside :func:`canmac.io.reader.get_view` — reads the same file.
    """
    if path is not None:
        os.environ["CANMAC_MANIFEST"] = str(Path(path).resolve())


def _zarray_shape(store: Path, component: str) -> tuple[int, ...]:
    zarray = store / component / ".zarray"
    if not zarray.exists():
        raise ValueError(f"missing .zarray: {zarray}")
    with open(zarray) as f:
        return tuple(json.load(f)["shape"])


def _validate_record(d: dict, base: Path) -> None:
    if not isinstance(d, dict):
        raise ValueError(f"each dataset must be a mapping, got {d!r}")
    did = d.get("id")
    if not isinstance(did, str) or not _SAFE_ID.match(did):
        raise ValueError(f"dataset id must be a filesystem-safe string, got {did!r}")
    for key in ("zarr_path", "dims", "channels"):
        if key not in d:
            raise ValueError(f"{did}: missing required key {key!r}")
    d.setdefault("component", "0/0")
    d.setdefault("parallel_unit", "timepoint")

    # Channels: colours recorded by omero hex (D-03), never by index.
    ch = d["channels"]
    if not isinstance(ch, dict):
        raise ValueError(f"{did}: channels must be a mapping")
    for role in _ROLES:
        col = str(ch.get(role, ""))
        if not _HEX.match(col):
            raise ValueError(f"{did}: channels.{role} must be a 6-digit hex colour, got {ch.get(role)!r}")
        ch[role] = col.upper()
    if ch["candida"] == ch["macrophage"]:
        raise ValueError(f"{did}: candida and macrophage share colour {ch['candida']}")
    discard = [str(c).upper() for c in (ch.get("discard") or [])]
    for col in discard:
        if not _HEX.match(col):
            raise ValueError(f"{did}: channels.discard entry {col!r} is not a 6-digit hex colour")
        if col in (ch["candida"], ch["macrophage"]):
            raise ValueError(f"{did}: colour {col} is both analysed and discarded")
    ch["discard"] = discard

    bc = d.get("bleach_correct")
    if bc is not None:
        if not isinstance(bc, dict) or not all(
            k in _ROLES and isinstance(v, bool) for k, v in bc.items()
        ):
            raise ValueError(f"{did}: bleach_correct must map candida/macrophage to true/false")

    # Parallelism unit is the timepoint (D-02), not the scene.
    if d["parallel_unit"] != "timepoint":
        raise ValueError(f"{did}: parallel_unit must be 'timepoint', got {d['parallel_unit']!r}")

    dims = d["dims"]
    if not isinstance(dims, dict) or not all(
        isinstance(dims.get(k), int) and dims[k] > 0 for k in _DIM_KEYS
    ):
        raise ValueError(f"{did}: dims must give positive integers for {', '.join(_DIM_KEYS)}")

    # Cross-check declared dims against the on-disk .zarray shape (T,C,Z,Y,X).
    store = (base / d["zarr_path"]).resolve()
    if not store.exists():
        raise ValueError(f"{did}: zarr_path does not exist: {store}")
    shape = _zarray_shape(store, d["component"])
    if len(shape) != 5:
        raise ValueError(f"{did}: expected a 5D (T,C,Z,Y,X) array, got shape {shape}")
    for name, actual in zip(_DIM_KEYS, shape):
        if dims[name] != actual:
            raise ValueError(
                f"{did}: dims.{name}={dims[name]} disagrees with on-disk .zarray {name}={actual} "
                f"(shape {shape})"
            )
    # Resolve the absolute store path for downstream consumers.
    d["_store"] = str(store)


def _validate(record: dict, base: Path) -> dict:
    if not isinstance(record, dict) or "datasets" not in record:
        raise ValueError("manifest must be a mapping with a 'datasets' key")
    datasets = record["datasets"]
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("manifest 'datasets' must be a non-empty list")
    for d in datasets:
        _validate_record(d, base)
    ids = [d["id"] for d in datasets]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate dataset ids: {dupes}")
    return record


def load_manifest(path: Optional[PathLike] = None) -> dict:
    """Load and validate the dataset manifest; return the parsed record.

    Paths inside the manifest are resolved relative to the manifest file's
    directory. Raises ``ValueError`` on any invariant drift.
    """
    manifest_path = Path(path) if path is not None else default_manifest_path()
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"manifest not found: {manifest_path} — copy manifest.example.yaml to manifest.yaml, "
            "or set CANMAC_MANIFEST"
        )
    with open(manifest_path) as f:
        record = yaml.safe_load(f)
    return _validate(record, base=manifest_path.resolve().parent)


def datasets(path: Optional[PathLike] = None) -> list[dict]:
    """Return the validated list of dataset records."""
    return load_manifest(path)["datasets"]


def ids(path: Optional[PathLike] = None) -> list[str]:
    """Dataset ids in manifest order."""
    return [d["id"] for d in datasets(path)]


def get(dataset_id: str, path: Optional[PathLike] = None) -> dict:
    """Return the validated record for ``dataset_id`` (e.g. "ROI2")."""
    records = datasets(path)
    for d in records:
        if d["id"] == dataset_id:
            return d
    raise ValueError(
        f"unknown dataset id {dataset_id!r}; manifest lists {[d['id'] for d in records]}"
    )


def resolve_dataset_id(dataset_id: Optional[str] = None, path: Optional[PathLike] = None) -> str:
    """Return ``dataset_id`` if it is in the manifest; if omitted, the manifest's only dataset.

    Lets every CLI make ``--dataset`` optional for single-dataset manifests while
    failing loud (listing the valid ids) when the choice is ambiguous or unknown.
    """
    if dataset_id is not None:
        return get(dataset_id, path)["id"]
    known = ids(path)
    if len(known) != 1:
        raise ValueError(f"--dataset is required: the manifest lists {known}")
    return known[0]


def n_timepoints(dataset_id: str, path: Optional[PathLike] = None) -> int:
    """Number of timepoints (``dims.T``) for ``dataset_id``."""
    return int(get(dataset_id, path)["dims"]["T"])


def add_cli_args(ap) -> None:
    """Add the standard ``--dataset`` / ``--manifest`` options to an argparse parser."""
    ap.add_argument("--dataset", default=None,
                    help="Dataset id from the manifest (optional if it lists only one).")
    ap.add_argument("--manifest", default=None,
                    help="Path to manifest.yaml (default: $CANMAC_MANIFEST, else repo root).")


def resolve_cli(ap, args) -> dict:
    """Apply ``--manifest`` process-wide, resolve ``--dataset``, return its record.

    Any manifest problem becomes a clean ``argparse`` error (exit 2) instead of a traceback.
    """
    use_manifest(getattr(args, "manifest", None))
    try:
        args.dataset = resolve_dataset_id(args.dataset)
        return get(args.dataset)
    except (ValueError, FileNotFoundError) as e:
        ap.error(str(e))
        raise  # unreachable: ap.error exits
