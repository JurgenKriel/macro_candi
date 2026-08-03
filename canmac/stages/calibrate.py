"""Emit a ``calibration.json`` sidecar per dataset (IO-02).

Combines the authoritative spatial parse (:func:`canmac.io.metadata.spatial_um`,
D-05) and time parse (:func:`canmac.io.metadata.timestamps_s`, D-06) into one
per-dataset ``results/{roi}/calibration.json`` sidecar so every downstream phase
expresses coordinates/volumes/rates in um / um3 / (um/min) from a single record.

All writes route through :mod:`canmac.io.atomic` (temp -> ``os.replace`` + a
``.done`` sentinel written LAST) — never a bare in-place write-mode file handle —
so an interrupted job never leaves a present-but-incomplete calibration.json that
a later stage would treat as done (T-01-02-03).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Union

from canmac.io.atomic import atomic_write_json, write_done
from canmac.io.metadata import spatial_um, timestamps_s

PathLike = Union[str, Path]


def emit_calibration(
    dataset_id: str,
    zattrs_path: PathLike,
    ome_xml_path: PathLike,
    out_dir: str = "results/{roi}",
) -> str:
    """Assemble and atomically write ``{out_dir}/calibration.json`` for one dataset.

    ``out_dir`` may contain a ``{roi}`` placeholder (default ``results/{roi}``)
    which is filled with ``dataset_id``. Returns the path to the written
    ``calibration.json``. The ``.done`` sentinel is written last (atomicity).
    """
    spatial = spatial_um(zattrs_path)
    time = timestamps_s(ome_xml_path)

    record: dict[str, Any] = {
        "dataset": dataset_id,
        "voxel_um": spatial["voxel_um"],
        "voxel_um3": spatial["voxel_um3"],
        "isotropic": spatial["isotropic"],
        "timestamps_s": time["timestamps_s"],
        "interval_min_mean": time["interval_min_mean"],
        "n_timepoints": time["n"],
        "units": {"length": "um", "volume": "um3", "rate": "um/min"},
        "source": {"scale": "NGFF multiscales", "time": "OME Plane/@DeltaT"},
    }

    resolved_dir = out_dir.format(roi=dataset_id)
    out_path = str(Path(resolved_dir) / "calibration.json")
    atomic_write_json(out_path, record)  # payload first ...
    write_done(out_path)  # ... sentinel LAST (D-07/atomicity)
    return out_path


def _paths_for(record: dict) -> tuple[str, str]:
    """Resolve the (.zattrs, OME XML) paths for a validated manifest record."""
    store = Path(record["_store"])
    return str(store / "0" / ".zattrs"), str(store / "OME" / "METADATA.ome.xml")


def main() -> None:
    """Emit a calibration.json for each dataset enumerated in the manifest."""
    from canmac.io.manifest import datasets

    parser = argparse.ArgumentParser(description="Emit per-dataset calibration.json sidecars.")
    parser.add_argument(
        "--manifest",
        default=None,
        help="Path to manifest.yaml (default: repo-root manifest.yaml).",
    )
    parser.add_argument(
        "--out-dir",
        default="results/{roi}",
        help="Output dir template (default: results/{roi}).",
    )
    args = parser.parse_args()

    for d in datasets(args.manifest):
        zattrs_path, ome_xml_path = _paths_for(d)
        out_path = emit_calibration(d["id"], zattrs_path, ome_xml_path, out_dir=args.out_dir)
        rec_voxel = spatial_um(zattrs_path)["voxel_um"]
        ts = timestamps_s(ome_xml_path)
        print(
            f"[{d['id']}] voxel={rec_voxel:.6f} um  isotropic=True  "
            f"n_t={ts['n']}  mean_interval={ts['interval_min_mean']:.4f} min  -> {out_path}"
        )


if __name__ == "__main__":
    main()
