"""Authoritative spatial + time calibration parse (D-05/D-06, IO-02).

Two silent-failure traps live in this phase's calibration, both closed here by
parsing the authoritative on-disk source and asserting invariants:

* **Spatial (D-05):** the isotropic 0.145 um voxel is read from the NGFF
  ``multiscales`` ``scale`` in ``raw.zarr/0/.zattrs``. Isotropy is *asserted*
  (max pairwise Z/Y/X scale diff < 1e-6 um) — a non-isotropic voxel slipping
  through silently corrupts every volume/rate measurement.
* **Time (D-06):** the true per-timepoint frame interval is read from the OME
  ``Plane/@DeltaT`` timestamps in ``raw.zarr/OME/METADATA.ome.xml`` — NOT the
  NGFF ``t`` scale (which is 1.0). Using the NGFF ``t`` scale makes dL/dt off by
  a constant factor (RESEARCH Pitfall 2).

Both parses use stdlib ``json`` + ``xml.etree.ElementTree`` only (RESEARCH
Pattern 1 — no ome-zarr-py object model whose keys churn across releases). The
OME XML is ~46 MB / 108,360 ``Plane`` elements, so it is parsed with **streaming**
``ET.iterparse`` + ``el.clear()`` rather than a full-DOM ``root.iter`` (RESEARCH
"Don't Hand-Roll"; avoids the analog's OOM-risky whole-DOM load).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Union

PathLike = Union[str, Path]

# Isotropy tolerance: max pairwise Z/Y/X scale difference (um).
_ISO_TOL_UM = 1e-6


def spatial_um(zattrs_path: PathLike) -> dict[str, Any]:
    """Read the isotropic voxel size (um) from the NGFF ``scale`` (D-05).

    Parses ``multiscales[0].datasets[0].coordinateTransformations[0].scale``
    (order ``t, c, z, y, x``) and asserts Z/Y/X isotropy to within ``_ISO_TOL_UM``.
    Raises ``AssertionError`` on anisotropy (fail loud, V5).

    Returns ``{"voxel_um": x, "voxel_um3": x*y*z, "isotropic": True}``.
    """
    with open(zattrs_path) as f:
        ds0 = json.load(f)["multiscales"][0]["datasets"][0]
    _, _, z, y, x = ds0["coordinateTransformations"][0]["scale"]  # t, c, z, y, x
    assert (
        abs(z - y) < _ISO_TOL_UM and abs(y - x) < _ISO_TOL_UM and abs(z - x) < _ISO_TOL_UM
    ), f"anisotropic voxel: {(z, y, x)} (tol {_ISO_TOL_UM} um)"
    return {"voxel_um": x, "voxel_um3": x * y * z, "isotropic": True}


def timestamps_s(ome_xml_path: PathLike) -> dict[str, Any]:
    """Read per-timepoint frame timestamps (s) from OME ``Plane/@DeltaT`` (D-06).

    Streams the OME XML with ``ET.iterparse`` + ``el.clear()`` (the file is ~46 MB
    / 108,360 ``Plane`` elements — a full-DOM parse is the analog's OOM-risky
    anti-pattern). ``DeltaT`` is constant across Z and C within a timepoint, so
    one value is taken per ``TheT`` at ``TheC == 0`` and ``TheZ == 0``; planes are
    indexed by their explicit ``TheT/TheC/TheZ`` attributes because the OME
    ``DimensionOrder`` (XYZCT) and Plane list order differ from the Zarr axis
    order (RESEARCH Pitfall 4).

    Asserts exactly 120 timestamps and strict monotonicity (fail loud, V5).

    Returns ``{"timestamps_s", "n", "interval_min", "interval_min_mean"}`` with
    intervals expressed in minutes (rate units are um/min downstream).
    """
    first: dict[int, float] = {}
    for _event, el in ET.iterparse(str(ome_xml_path), events=("end",)):
        if el.tag.split("}")[-1] == "Plane":
            t = int(el.get("TheT"))
            if int(el.get("TheC")) == 0 and int(el.get("TheZ")) == 0 and t not in first:
                first[t] = float(el.get("DeltaT"))  # DeltaTUnit="s"
            el.clear()

    ts = [first[t] for t in sorted(first)]
    n = len(ts)
    assert n == 120, f"expected 120 timestamps, got {n}"
    assert all(ts[i + 1] > ts[i] for i in range(n - 1)), "timestamps not monotonic increasing"

    interval_min = [(ts[i + 1] - ts[i]) / 60.0 for i in range(n - 1)]
    interval_min_mean = sum(interval_min) / len(interval_min)
    return {
        "timestamps_s": ts,
        "n": n,
        "interval_min": interval_min,
        "interval_min_mean": interval_min_mean,
    }
