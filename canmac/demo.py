"""Synthetic demo dataset — generate a tiny timelapse with a known answer, then score the pipeline.

The real lattice light-sheet stores are ~30 GB and unpublished, so this module builds a small
stand-in (24 frames of 48 x 128 x 128 voxels, a few MB) with the SAME on-disk layout the
pipeline reads (bioformats2raw-style OME-Zarr v2, big-endian ``>u2``, omero channel colours,
per-plane ``DeltaT`` in ``OME/METADATA.ome.xml``) and a scene whose answer is known exactly:

  * three macrophages (green ``00FF00``) that photobleach (~halve over the movie);
  * three yeast (red ``FF0000``): one engulfed throughout, one free, and one that migrates
    into a macrophage (free -> partial -> engulfed);
  * two hyphae (red) growing at fixed rates, one of which branches at frame 8;
  * a decoy "lysed" channel (magenta ``FF00FF``) stored FIRST and overlapping a macrophage —
    if channel resolution ever fell back to index, the macrophage census would change.

Usage (from the repo root)::

    python -m canmac.demo generate              # -> data/demo/{manifest.yaml, DEMO/raw.zarr, ground_truth.json}
    CANMAC_MANIFEST=data/demo/manifest.yaml ...  # run the stages (see scripts/demo/run_demo.sh)
    python -m canmac.demo check                 # score results/DEMO against the ground truth

The scene is deterministic (fixed seed), so ``check`` re-renders the true masks rather than
storing them. **Synthetic data validates plumbing and units, not biology**: passing ``check``
says the pipeline measures a clean, known scene correctly — not that it is right on real data.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Optional

import numpy as np

DATASET_ID = "DEMO"
SHAPE_ZYX = (48, 128, 128)
VOXEL_UM = 0.5
N_T = 24
INTERVAL_S = 180.0
SEED = 20260726
COLORS = ["FF00FF", "FF0000", "00FF00"]  # lysed, candida, macrophage — the real on-disk order
ENGULF_FRAC, TOUCH_FRAC = 0.9, 0.05      # the engulfment stage defaults

BACKGROUND = 100.0
MAC_INTENSITY = 1400.0
MAC_BLEACH_TAU = 30.0                     # frames; macrophage signal ~ exp(-t / tau)
CANDIDA_INTENSITY = 1000.0
PSF_SIGMA = 1.0                           # voxels

MACROPHAGES = {                           # name: (centre zyx, radii zyx), voxels
    "M1": ((24, 34, 34), (10, 14, 14)),
    "M2": ((24, 34, 94), (10, 14, 14)),
    "M3": ((24, 100, 30), (10, 14, 14)),
}
YEAST_RADIUS = 3.5


def yeast_centres(t: int) -> dict[str, tuple[float, float, float]]:
    """Yeast centres at frame t: Y1 inside M1, Y2 migrating into M2 along -x, Y3 free."""
    return {
        "Y1": (24, 34, 34),
        "Y2": (24, 34, 116 - 1.0 * t),
        "Y3": (24, 118, 60),
    }


def hypha_segments(t: int) -> dict[str, list[tuple[np.ndarray, np.ndarray]]]:
    """Centreline segments (start, end; zyx voxels) of each hypha at frame t."""
    def seg(start, direction, length):
        p0 = np.asarray(start, dtype=float)
        d = np.asarray(direction, dtype=float)
        return p0, p0 + d / np.linalg.norm(d) * length

    h1 = [seg((23, 100, 66), (0.08, 0, 1), 12 + 1.5 * t)]   # 1.5 vox/frame = 0.25 um/min
    h2 = [seg((24, 66, 50), (0, 0, 1), 14 + 1.0 * t)]       # 1.0 vox/frame = 0.167 um/min
    if t >= 8:                                              # branch appears at frame 8
        h2.append(seg((24, 66, 62), (0, 1, 0.5), 3 + 1.0 * (t - 8)))
    return {"H1": h1, "H2": h2}


def hypha_length_um(t: int) -> dict[str, float]:
    """True centreline length (um) of each hypha at frame t."""
    return {name: float(sum(np.linalg.norm(b - a) for a, b in segs)) * VOXEL_UM
            for name, segs in hypha_segments(t).items()}


def hypha_anchor(name: str) -> tuple[int, int]:
    """A (y, x) pixel on the hypha's base that persists in every frame's MIP."""
    (p0, _p1) = hypha_segments(0)[name][0]
    return int(round(p0[1])), int(round(p0[2] + 4))


def _grid():
    z, y, x = np.ogrid[: SHAPE_ZYX[0], : SHAPE_ZYX[1], : SHAPE_ZYX[2]]
    return z, y, x


def _ellipsoid(centre, radii) -> np.ndarray:
    z, y, x = _grid()
    (cz, cy, cx), (rz, ry, rx) = centre, radii
    return ((z - cz) / rz) ** 2 + ((y - cy) / ry) ** 2 + ((x - cx) / rx) ** 2 <= 1.0


def _tube(segments, radius: float = 1.6) -> np.ndarray:
    pts = np.stack(np.meshgrid(*(np.arange(s) for s in SHAPE_ZYX), indexing="ij"), -1).astype(float)
    mask = np.zeros(SHAPE_ZYX, dtype=bool)
    for a, b in segments:
        ab = b - a
        u = np.clip(((pts - a) @ ab) / max(float(ab @ ab), 1e-9), 0.0, 1.0)
        nearest = a + u[..., None] * ab
        mask |= np.linalg.norm(pts - nearest, axis=-1) <= radius
    return mask


def true_masks(t: int) -> dict[str, dict[str, np.ndarray]]:
    """Noise-free object masks at frame t: ``{"macrophage": {...}, "candida": {...}}``."""
    mac = {name: _ellipsoid(c, r) for name, (c, r) in MACROPHAGES.items()}
    cand = {name: _ellipsoid(c, (YEAST_RADIUS,) * 3) for name, c in yeast_centres(t).items()}
    cand.update({name: _tube(segs) for name, segs in hypha_segments(t).items()})
    return {"macrophage": mac, "candida": cand}


def true_class(cand_mask: np.ndarray, mac_masks: dict[str, np.ndarray]) -> tuple[float, str]:
    """(best overlap fraction, class) with the engulfment stage's default thresholds."""
    n = int(cand_mask.sum())
    frac = max((int((cand_mask & m).sum()) / n for m in mac_masks.values()), default=0.0)
    cls = "engulfed" if frac >= ENGULF_FRAC else "partial" if frac >= TOUCH_FRAC else "free"
    return round(frac, 4), cls


def render(t: int, rng: np.random.Generator) -> np.ndarray:
    """One noisy (C, Z, Y, X) uint16 frame in the on-disk channel order (lysed, candida, macrophage)."""
    from scipy.ndimage import gaussian_filter

    masks = true_masks(t)
    mac = np.zeros(SHAPE_ZYX, np.float32)
    for m in masks["macrophage"].values():
        mac[m] = MAC_INTENSITY * math.exp(-t / MAC_BLEACH_TAU)
    cand = np.zeros(SHAPE_ZYX, np.float32)
    for m in masks["candida"].values():
        cand[m] = CANDIDA_INTENSITY
    lysed = np.zeros(SHAPE_ZYX, np.float32)
    lysed[_ellipsoid((24, 40, 40), (8, 8, 8))] = 3000.0     # overlaps M1 on purpose
    lysed[_ellipsoid((24, 64, 100), (10, 10, 10))] = 3000.0

    out = []
    for img in (lysed, cand, mac):
        img = gaussian_filter(img, PSF_SIGMA) + BACKGROUND
        out.append(np.clip(rng.poisson(img), 0, 65535).astype(np.uint16))
    return np.stack(out)


def timestamps_s() -> list[float]:
    """Real-looking frame times: ~3 min apart with a little acquisition jitter."""
    rng = np.random.default_rng(SEED + 1)
    jitter = rng.uniform(-1.5, 1.5, N_T)
    jitter[0] = 0.0
    return [round(t * INTERVAL_S + float(j), 3) for t, j in enumerate(jitter)]


def _ome_xml(times: list[float]) -> str:
    z, y, x = SHAPE_ZYX
    planes = "\n".join(
        f'      <Plane TheT="{t}" TheC="{c}" TheZ="{zz}" DeltaT="{times[t]}" DeltaTUnit="s"/>'
        for t in range(N_T) for c in range(len(COLORS)) for zz in range(z)
    )
    channels = "\n".join(
        f'      <Channel ID="Channel:0:{i}" Name="{n}" SamplesPerPixel="1"/>'
        for i, n in enumerate(["lysed", "candida", "macrophage"])
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">
  <Image ID="Image:0" Name="{DATASET_ID} (synthetic)">
    <Pixels ID="Pixels:0" DimensionOrder="XYZCT" Type="uint16" BigEndian="true"
            SizeX="{x}" SizeY="{y}" SizeZ="{z}" SizeC="{len(COLORS)}" SizeT="{N_T}"
            PhysicalSizeX="{VOXEL_UM}" PhysicalSizeY="{VOXEL_UM}" PhysicalSizeZ="{VOXEL_UM}"
            PhysicalSizeXUnit="µm" PhysicalSizeYUnit="µm" PhysicalSizeZUnit="µm">
{channels}
{planes}
    </Pixels>
  </Image>
</OME>
'''


def _write_store(store: Path) -> None:
    import zarr

    root = zarr.open_group(str(store), mode="w", zarr_format=2)
    root.attrs["bioformats2raw.layout"] = 3
    img = root.create_group("0")
    img.attrs.update({
        "multiscales": [{
            "version": "0.4",
            "name": DATASET_ID,
            "axes": [{"name": "t", "type": "time"}, {"name": "c", "type": "channel"},
                     *({"name": a, "type": "space", "unit": "micrometer"} for a in "zyx")],
            "datasets": [{"path": "0", "coordinateTransformations": [
                {"type": "scale", "scale": [1.0, 1.0, VOXEL_UM, VOXEL_UM, VOXEL_UM]}]}],
        }],
        "omero": {"channels": [
            {"color": col, "label": lab, "active": True,
             "window": {"start": 0, "end": 4000, "min": 0, "max": 65535}}
            for col, lab in zip(COLORS, ["lysed", "candida", "macrophage"])
        ]},
    })
    arr = img.create_array(
        "0", shape=(N_T, len(COLORS), *SHAPE_ZYX), chunks=(1, 1, *SHAPE_ZYX), dtype=">u2",
        chunk_key_encoding={"name": "v2", "separator": "/"}, fill_value=0,
    )
    rng = np.random.default_rng(SEED)
    for t in range(N_T):
        arr[t] = render(t, rng)
    (store / "OME").mkdir(parents=True, exist_ok=True)
    (store / "OME" / "METADATA.ome.xml").write_text(_ome_xml(timestamps_s()))


def ground_truth() -> dict:
    """Per-frame truth: yeast classes, hypha lengths/tips/junctions, and the expected rates."""
    times = timestamps_s()
    frames = []
    for t in range(N_T):
        masks = true_masks(t)
        objects = {}
        for name in ("Y1", "Y2", "Y3", "H1", "H2"):
            frac, cls = true_class(masks["candida"][name], masks["macrophage"])
            objects[name] = {"overlap_frac": frac, "class": cls}
        frames.append({"t": t, "time_s": times[t], "objects": objects,
                       "hypha_length_um": hypha_length_um(t)})
    minutes = np.array(times) / 60.0
    rates = {name: round(float(np.polyfit(minutes, [f["hypha_length_um"][name] for f in frames], 1)[0]), 4)
             for name in ("H1", "H2")}
    return {
        "dataset": DATASET_ID, "voxel_um": VOXEL_UM, "n_t": N_T,
        "n_macrophages": len(MACROPHAGES), "n_candida_objects": 5,
        "growth_rate_um_per_min": rates,
        "macrophage_bleach": {"model": "exp(-t/tau)", "tau_frames": MAC_BLEACH_TAU},
        "frames": frames,
    }


def generate(out: Path, force: bool = False) -> Path:
    """Write ``out/manifest.yaml``, ``out/DEMO/raw.zarr`` and ``out/ground_truth.json``."""
    import yaml

    store = out / DATASET_ID / "raw.zarr"
    if store.exists():
        if not force:
            raise SystemExit(f"{store} exists — pass --force to regenerate")
        shutil.rmtree(store)
    store.parent.mkdir(parents=True, exist_ok=True)
    _write_store(store)
    z, y, x = SHAPE_ZYX
    manifest = {"datasets": [{
        "id": DATASET_ID,
        "zarr_path": f"{DATASET_ID}/raw.zarr",
        "component": "0/0",
        "dims": {"T": N_T, "C": len(COLORS), "Z": z, "Y": y, "X": x},
        "voxel_um": VOXEL_UM,
        "channels": {"candida": COLORS[1], "macrophage": COLORS[2], "discard": [COLORS[0]]},
        "bleach_correct": {"macrophage": True, "candida": False},
        "parallel_unit": "timepoint",
    }]}
    (out / "manifest.yaml").write_text(
        "# Synthetic demo dataset — generated by `python -m canmac.demo generate`.\n"
        + yaml.safe_dump(manifest, sort_keys=False))
    (out / "ground_truth.json").write_text(json.dumps(ground_truth(), indent=1))
    return out / "manifest.yaml"


# ---------------------------------------------------------------------------- check


def _label_at(store: Path, t: int, zyx) -> int:
    import zarr

    root = zarr.open_group(str(store), mode="r")
    key = f"t{t:03d}"
    if key not in root:
        return 0
    return int(root[key][tuple(int(round(v)) for v in zyx)])


def check(results: Path, gt_path: Path, rate_tol: float = 0.25, class_min: float = 0.85) -> bool:
    """Score ``results`` against the ground truth; print a report; return True on pass."""
    import pandas as pd

    gt = json.loads(gt_path.read_text())
    ok = True
    rows: list[tuple[str, str, str, bool]] = []

    def report(name, expected, got, passed):
        nonlocal ok
        ok &= bool(passed)
        rows.append((name, str(expected), str(got), bool(passed)))

    # 1. calibration: real units and real time
    cal = json.loads((results / "calibration.json").read_text())
    report("voxel size (um)", gt["voxel_um"], round(cal["voxel_um"], 4),
           abs(cal["voxel_um"] - gt["voxel_um"]) < 1e-6)
    report("timepoints", gt["n_t"], cal["n_timepoints"], cal["n_timepoints"] == gt["n_t"])
    report("mean interval (min)", round(INTERVAL_S / 60, 3), round(cal["interval_min_mean"], 3),
           abs(cal["interval_min_mean"] - INTERVAL_S / 60) < 0.05)

    # 2. bleaching: macrophage decay detected and corrected; candida left raw
    bl = json.loads((results / "bleach.json").read_text())["channels"]
    f_last = bl["macrophage"]["factors"][str(gt["n_t"] - 1)]
    report("macrophage bleach factor, last frame", "> 1.5", round(f_last, 2), f_last > 1.5)
    report("bleach policy (macrophage / candida)", "apply / raw",
           f"{'apply' if bl['macrophage']['apply'] else 'raw'} / "
           f"{'apply' if bl['candida']['apply'] else 'raw'}",
           bl["macrophage"]["apply"] and not bl["candida"]["apply"])

    # 3. census: right number of objects every frame (and the decoy channel never read)
    census = pd.read_csv(results / "census.csv")
    for ch, n in (("macrophage", gt["n_macrophages"]), ("candida", gt["n_candida_objects"])):
        counts = census[census["channel"] == ch]["n_objects"]
        good = int((counts == n).sum())
        report(f"{ch} count = {n}", f"{gt['n_t']}/{gt['n_t']} frames", f"{good}/{len(counts)} frames",
               len(counts) == gt["n_t"] and good >= 0.9 * gt["n_t"])

    # 4. engulfment: per-object class agreement, matched by the label at each object's centre
    eng = pd.read_csv(results / "engulfment.csv")
    agree = total = 0
    mismatches = []
    for fr in gt["frames"]:
        t = fr["t"]
        for name, centre in yeast_centres(t).items():
            lab = _label_at(results / "candida_labels.zarr", t, centre)
            hit = eng[(eng["t"] == t) & (eng["candida"] == lab)]
            got = hit["PROPOSED_class"].iloc[0] if lab and len(hit) else "missing"
            want = fr["objects"][name]["class"]
            total += 1
            agree += got == want
            if got != want:
                mismatches.append(f"t{t:02d} {name}: want {want}, got {got}")
    report("engulfment class agreement (yeast)", f">= {class_min:.0%}",
           f"{agree}/{total} ({agree / total:.0%})", agree / total >= class_min)
    y2 = [fr["objects"]["Y2"]["class"] for fr in gt["frames"]]
    report("Y2 migrates free -> partial -> engulfed", "all three seen", sorted(set(y2)),
           {"free", "partial", "engulfed"} <= set(y2))

    # 5. hyphal growth: tracked rate vs the true rate
    tracks = pd.read_csv(results / "hyphae2d_tracks.csv")
    rates = pd.read_csv(results / "hyphae2d_growth_rates.csv")
    import zarr

    mip_labels = zarr.open_group(str(results / "candida_mip_labels.zarr"), mode="r")
    last = gt["n_t"] - 1
    for name in ("H1", "H2"):
        y, x = hypha_anchor(name)
        obj = int(mip_labels[f"t{last:03d}"][y, x])
        tid = tracks[(tracks["t"] == last) & (tracks["obj"] == obj)]["track_id"]
        want = gt["growth_rate_um_per_min"][name]
        got = None
        if obj and len(tid):
            r = rates[rates["track_id"] == int(tid.iloc[0])]["um_per_min"]
            got = float(r.iloc[0]) if len(r) else None
        passed = got is not None and abs(got - want) <= rate_tol * want
        report(f"{name} growth rate (um/min)", f"{want} ± {rate_tol:.0%}",
               "no track" if got is None else round(got, 4), passed)

    w = max(len(r[0]) for r in rows)
    print(f"\nSynthetic demo check — {results}\n")
    for name, exp, got, passed in rows:
        print(f"  {'PASS' if passed else 'FAIL'}  {name:<{w}}  expected {exp:<22} got {got}")
    if mismatches:
        print(f"\n  class mismatches ({len(mismatches)}):")
        for m in mismatches[:10]:
            print(f"    {m}")
    print(f"\n{'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}\n")
    return ok


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m canmac.demo", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="Write the synthetic dataset, manifest and ground truth.")
    g.add_argument("--out", default="data/demo", help="Output directory (default: data/demo).")
    g.add_argument("--force", action="store_true", help="Overwrite an existing demo store.")
    c = sub.add_parser("check", help="Score pipeline results against the ground truth.")
    c.add_argument("--out", default="data/demo", help="Where `generate` wrote (default: data/demo).")
    c.add_argument("--results", default=f"results/{DATASET_ID}",
                   help=f"Results directory (default: results/{DATASET_ID}).")
    args = ap.parse_args(argv)

    if args.cmd == "generate":
        manifest = generate(Path(args.out), force=args.force)
        print(f"wrote {manifest}\n  next: CANMAC_MANIFEST={manifest} bash scripts/demo/run_demo.sh")
        return 0
    return 0 if check(Path(args.results), Path(args.out) / "ground_truth.json") else 1


if __name__ == "__main__":
    sys.exit(main())
