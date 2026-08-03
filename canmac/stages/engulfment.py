"""Phase-5 prototype — 3D containment of candida inside macrophages (engulfment/entrapment).

Reads the two per-timepoint label stores produced by the segmentation stage and, for ONE
timepoint, measures how much of each candida object lies inside each macrophage. Emits a
per-(candida, macrophage) table plus a per-candida class.

**All output is PROPOSED, not validated** — containment fractions are geometry, but whether a
given pair is a real engulfment event is a biological judgement the scientist confirms visually
(see `view_engulfment.py`). This is a per-timepoint prototype: the *persistence* test
(contained for >= N consecutive frames, which distinguishes true engulfment from a transient
overlap) and fate classification come later with tracking.

Classes (per candida object, from the best-overlapping macrophage):
  * ``engulfed``  — overlap fraction >= ``engulf_frac`` (default 0.9): essentially fully inside.
  * ``partial``   — ``touch_frac`` <= fraction < ``engulf_frac``: partially internalized /
    entrapped (a phagocytic cup, or a hypha threading through the macrophage).
  * ``free``      — fraction < ``touch_frac``: not associated with any macrophage.

Geometry notes:
  * Cellpose macrophage masks are FILLED volumes, so a candida object inside the cell overlaps
    the macrophage label directly — no cavity handling needed.
  * ``dilate`` optionally grows each macrophage by N voxels before testing, to catch candida
    pressed against the membrane (adhesion vs internalization). Off by default.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np

from canmac.io.atomic import atomic_write_json, atomic_write_text, write_done

_LOG = logging.getLogger(__name__)

ENGULF_FRAC = 0.9   # >= this fraction inside -> engulfed
TOUCH_FRAC = 0.05   # >= this (and < ENGULF_FRAC) -> partial/entrapped


def load_labels(dataset: str, channel: str, t: int, out_dir: str = "results/{roi}",
                store_name: Optional[str] = None) -> Optional[np.ndarray]:
    """Read ONE timepoint's label volume, or None if that timepoint isn't segmented yet."""
    import zarr

    resolved = out_dir.format(roi=dataset) if "{roi}" in out_dir else out_dir
    store = f"{resolved}/{store_name or (channel + '_labels')}.zarr"
    if not Path(store).exists():
        return None
    root = zarr.open_group(store, mode="r")
    key = f"t{t:03d}"
    if key not in root:
        return None
    return root[key][:]


def containment(
    macrophage: np.ndarray,
    candida: np.ndarray,
    engulf_frac: float = ENGULF_FRAC,
    touch_frac: float = TOUCH_FRAC,
    dilate: int = 0,
):
    """Per-candida containment vs macrophage labels -> (pairs DataFrame, per-candida DataFrame).

    Computes, for every candida label, the fraction of its voxels falling inside each
    macrophage label (a single vectorized pass over candida voxels), then classifies each
    candida object by its best-overlapping macrophage.
    """
    import pandas as pd

    if dilate > 0:
        from scipy.ndimage import grey_dilation

        macrophage = grey_dilation(macrophage, size=(2 * dilate + 1,) * 3)

    cand_vox = candida > 0
    pairs: list[dict[str, Any]] = []
    if cand_vox.any():
        c_flat = candida[cand_vox].astype(np.int64)
        m_flat = macrophage[cand_vox].astype(np.int64)
        # one pass: count (candida_label, macrophage_label) co-occurrences
        keyed = c_flat * (int(macrophage.max()) + 1) + m_flat
        uniq, counts = np.unique(keyed, return_counts=True)
        base = int(macrophage.max()) + 1
        totals = dict(zip(*np.unique(c_flat, return_counts=True)))
        for k, n in zip(uniq, counts):
            c_lab, m_lab = int(k // base), int(k % base)
            if m_lab == 0:
                continue  # candida voxels in background — not a pair
            pairs.append({"candida": c_lab, "macrophage": m_lab, "overlap_vox": int(n),
                          "candida_vox": int(totals[c_lab]),
                          "overlap_frac": round(float(n) / float(totals[c_lab]), 4)})
    pairs_df = pd.DataFrame(pairs, columns=["candida", "macrophage", "overlap_vox",
                                            "candida_vox", "overlap_frac"])

    rows = []
    for c_lab in (np.unique(candida[cand_vox]) if cand_vox.any() else []):
        c_lab = int(c_lab)
        sub = pairs_df[pairs_df["candida"] == c_lab]
        if len(sub):
            best = sub.loc[sub["overlap_frac"].idxmax()]
            frac, m_lab = float(best["overlap_frac"]), int(best["macrophage"])
        else:
            frac, m_lab = 0.0, 0
        cls = ("engulfed" if frac >= engulf_frac
               else "partial" if frac >= touch_frac
               else "free")
        rows.append({"candida": c_lab, "candida_vox": int((candida == c_lab).sum()),
                     "best_macrophage": m_lab, "overlap_frac": round(frac, 4),
                     "PROPOSED_class": cls})
    per_c = pd.DataFrame(rows, columns=["candida", "candida_vox", "best_macrophage",
                                        "overlap_frac", "PROPOSED_class"])
    if not per_c.empty:
        per_c = per_c.sort_values("overlap_frac", ascending=False).reset_index(drop=True)
    return pairs_df, per_c


def process_timepoint(dataset: str, t: int, out_dir: str = "results/{roi}",
                      candida_store: Optional[str] = None, **kw):
    """Containment for ONE timepoint -> writes a per-t JSON shard; returns the per-candida table.

    Returns None when either channel's timepoint is not segmented yet (so a batch can skip
    ahead rather than fail). Shards avoid append races under a SLURM array; `finalize` concats.
    """
    mac = load_labels(dataset, "macrophage", t, out_dir)
    cand = load_labels(dataset, "candida", t, out_dir, store_name=candida_store)
    if mac is None or cand is None:
        _LOG.info("t%03d: missing labels (macrophage=%s candida=%s) — skipping",
                  t, mac is not None, cand is not None)
        return None
    _pairs, per_c = containment(mac, cand, **kw)
    resolved = out_dir.format(roi=dataset) if "{roi}" in out_dir else out_dir
    shard = Path(resolved) / "engulfment" / f"t{t:03d}.json"
    payload = {"dataset": dataset, "t": int(t),
               "n_candida": int(len(per_c)),
               "n_engulfed": int((per_c["PROPOSED_class"] == "engulfed").sum()) if len(per_c) else 0,
               "n_partial": int((per_c["PROPOSED_class"] == "partial").sum()) if len(per_c) else 0,
               "n_free": int((per_c["PROPOSED_class"] == "free").sum()) if len(per_c) else 0,
               "objects": per_c.to_dict("records")}
    atomic_write_json(shard, payload)
    write_done(str(shard))  # LAST
    return per_c


def finalize(dataset: str, out_dir: str = "results/{roi}") -> str:
    """Concat per-t engulfment shards into ``{out_dir}/engulfment.csv`` (one row per (t, candida))."""
    import json

    import pandas as pd

    resolved = out_dir.format(roi=dataset) if "{roi}" in out_dir else out_dir
    rows = []
    d = Path(resolved) / "engulfment"
    if d.exists():
        for shard in sorted(d.glob("t*.json")):
            payload = json.loads(shard.read_text())
            for o in payload["objects"]:
                rows.append({"dataset": payload["dataset"], "t": payload["t"], **o})
    cols = ["dataset", "t", "candida", "candida_vox", "best_macrophage", "overlap_frac",
            "PROPOSED_class"]
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df = df.sort_values(["t", "candida"]).reset_index(drop=True)
    out = Path(resolved) / "engulfment.csv"
    atomic_write_text(str(out), df.to_csv(index=False))
    return str(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Phase-5 prototype: 3D containment of candida in macrophages (PROPOSED).")
    ap.add_argument("--dataset", default="ROI2", choices=["ROI2", "ROI7"])
    ap.add_argument("--t", type=int, help="Single timepoint; omit with --all-t to sweep.")
    ap.add_argument("--all-t", action="store_true", dest="all_t",
                    help="Process every timepoint that has BOTH channels segmented.")
    ap.add_argument("--candida-store", default=None, dest="candida_store",
                    help="Alternate candida store name (e.g. ms_localstd_masked) — default candida_labels.")
    ap.add_argument("--engulf-frac", type=float, default=ENGULF_FRAC, dest="engulf_frac")
    ap.add_argument("--touch-frac", type=float, default=TOUCH_FRAC, dest="touch_frac")
    ap.add_argument("--dilate", type=int, default=0,
                    help="Grow macrophages N voxels before testing (adhesion vs internalization).")
    ap.add_argument("--out-dir", default="results/{roi}", dest="out_dir")
    ap.add_argument("--finalize", action="store_true", help="Concat shards into engulfment.csv.")
    args = ap.parse_args()

    if args.finalize:
        print("wrote", finalize(args.dataset, args.out_dir))
        return

    kw = dict(engulf_frac=args.engulf_frac, touch_frac=args.touch_frac, dilate=args.dilate)
    ts = range(120) if args.all_t else ([args.t] if args.t is not None else [])
    if not ts:
        ap.error("give --t N, or --all-t, or --finalize")
    done = 0
    for t in ts:
        per_c = process_timepoint(args.dataset, t, args.out_dir,
                                  candida_store=args.candida_store, **kw)
        if per_c is None:
            continue
        done += 1
        n_e = int((per_c["PROPOSED_class"] == "engulfed").sum()) if len(per_c) else 0
        n_p = int((per_c["PROPOSED_class"] == "partial").sum()) if len(per_c) else 0
        print(f"t{t:03d}: {len(per_c)} candida -> PROPOSED engulfed={n_e} partial={n_p} "
              f"free={len(per_c)-n_e-n_p}")
    print(f"processed {done} timepoint(s) — PROPOSED, confirm visually (view_engulfment.py)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
