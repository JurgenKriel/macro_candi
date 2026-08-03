"""Phase-6 — link 2D hyphal objects ACROSS timepoints to get per-object growth trajectories.

`hyphae2d` measures each object per frame, but its ids are per-frame connected-component
labels — object 3 at t40 is unrelated to object 3 at t41. This module links them into
**tracks** so each hypha has its own length-vs-time curve (the actual growth readout).

Linking method — **overlap (IoU) between consecutive frames**. Hyphae are effectively
sessile and grow by extension, so at a 3-min frame interval a filament overlaps its own
previous mask heavily; overlap linking is far more robust here than centroid/particle
linking (a growing filament's centroid moves as it extends, which trips distance-based
linkers). Each object at t is linked to the previous-frame object with the largest IoU
above ``min_iou``; unmatched objects start a new track.

Merges and splits are recorded, not hidden: when two tracks map onto one object the event
is flagged ``merge`` (hyphal fusion/anastomosis, or two filaments touching in projection),
and one object claimed by two successors is flagged ``split``. Growth curves for tracks
involved in a merge should be read with that in mind.

**PROPOSED output** — a track is only meaningful if the underlying per-frame masks are
right; confirm visually before trusting any growth rate.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Optional

import numpy as np

from canmac.io.atomic import atomic_write_text, write_done

_LOG = logging.getLogger(__name__)


def load_frame_labels(dataset: str, t: int, out_dir: str = "results/{roi}",
                      store_name: str = "candida_mip_labels") -> Optional[np.ndarray]:
    """Read the saved 2D label frame for timepoint t (or None if absent)."""
    import zarr

    resolved = out_dir.format(roi=dataset) if "{roi}" in out_dir else out_dir
    store = f"{resolved}/{store_name}.zarr"
    if not Path(store).exists():
        return None
    root = zarr.open_group(store, mode="r")
    key = f"t{t:03d}"
    return root[key][:] if key in root else None


def link_frames(prev: np.ndarray, curr: np.ndarray, min_iou: float = 0.1) -> dict[int, int]:
    """Map ``curr`` label -> best-matching ``prev`` label by IoU (empty when no match).

    One vectorized pass over the overlap of the two label images.
    """
    matches: dict[int, int] = {}
    both = (prev > 0) & (curr > 0)
    if not both.any():
        return matches
    p = prev[both].astype(np.int64)
    c = curr[both].astype(np.int64)
    base = int(prev.max()) + 1
    uniq, counts = np.unique(c * base + p, return_counts=True)
    prev_area = dict(zip(*np.unique(prev[prev > 0], return_counts=True)))
    curr_area = dict(zip(*np.unique(curr[curr > 0], return_counts=True)))
    best: dict[int, tuple[float, int]] = {}
    for k, inter in zip(uniq, counts):
        c_lab, p_lab = int(k // base), int(k % base)
        union = curr_area[c_lab] + prev_area[p_lab] - inter
        iou = float(inter) / float(union) if union else 0.0
        if iou >= min_iou and iou > best.get(c_lab, (0.0, 0))[0]:
            best[c_lab] = (iou, p_lab)
    for c_lab, (_iou, p_lab) in best.items():
        matches[c_lab] = p_lab
    return matches


def build_tracks(dataset: str, t_start: int = 0, t_stop: int = 120,
                 out_dir: str = "results/{roi}", min_iou: float = 0.1,
                 store_name: str = "candida_mip_labels"):
    """Link objects across all frames -> DataFrame of (t, obj, track_id, event)."""
    import pandas as pd

    rows = []
    prev_lab: Optional[np.ndarray] = None
    prev_track: dict[int, int] = {}      # prev-frame object label -> track_id
    next_track = 1
    for t in range(t_start, t_stop):
        curr = load_frame_labels(dataset, t, out_dir, store_name)
        if curr is None:
            continue
        curr_track: dict[int, int] = {}
        matches = link_frames(prev_lab, curr, min_iou) if prev_lab is not None else {}
        claimed: dict[int, list[int]] = {}
        for c_lab, p_lab in matches.items():
            claimed.setdefault(p_lab, []).append(c_lab)
        for c_lab in np.unique(curr[curr > 0]):
            c_lab = int(c_lab)
            p_lab = matches.get(c_lab)
            event = ""
            if p_lab is None:
                tid = next_track; next_track += 1
                event = "start"
            else:
                siblings = claimed.get(p_lab, [])
                if len(siblings) > 1 and c_lab != siblings[0]:
                    tid = next_track; next_track += 1   # split: continuation keeps the id
                    event = "split"
                else:
                    tid = prev_track.get(p_lab)
                    if tid is None:
                        tid = next_track; next_track += 1; event = "start"
            curr_track[c_lab] = tid
            rows.append({"dataset": dataset, "t": int(t), "obj": c_lab, "track_id": int(tid),
                         "event": event})
        # merge detection: two distinct previous tracks landing on one current object
        inv: dict[int, list[int]] = {}
        for c_lab, p_lab in matches.items():
            inv.setdefault(c_lab, []).append(p_lab)
        for c_lab, plist in inv.items():
            tids = {prev_track.get(p) for p in plist if prev_track.get(p)}
            if len(tids) > 1:
                for r in rows:
                    if r["t"] == t and r["obj"] == c_lab:
                        r["event"] = "merge"
        prev_lab, prev_track = curr, curr_track
    return pd.DataFrame(rows, columns=["dataset", "t", "obj", "track_id", "event"])


def finalize(dataset: str, out_dir: str = "results/{roi}", min_frames: int = 3,
             **kw) -> str:
    """Build tracks, join the per-object measurements, write CSV + per-track growth plots."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    resolved = out_dir.format(roi=dataset) if "{roi}" in out_dir else out_dir
    tracks = build_tracks(dataset, out_dir=out_dir, **kw)
    if tracks.empty:
        _LOG.warning("no tracks (are the 2D label frames built?)")
        return ""
    obj_csv = Path(resolved) / "hyphae2d_objects.csv"
    if not obj_csv.exists():
        _LOG.warning("missing %s — run hyphae2d --finalize first", obj_csv)
        return ""
    objs = pd.read_csv(obj_csv)
    df = objs.merge(tracks, on=["t", "obj"], how="inner", suffixes=("", "_trk"))
    out = Path(resolved) / "hyphae2d_tracks.csv"
    atomic_write_text(str(out), df.sort_values(["track_id", "t"]).to_csv(index=False))

    # per-track growth curves (only tracks seen in >= min_frames frames)
    keep = df.groupby("track_id").size()
    keep = keep[keep >= min_frames].index.tolist()
    sub = df[df["track_id"].isin(keep)].sort_values(["track_id", "t"])
    fig, ax = plt.subplots(1, 2, figsize=(14, 5))
    for tid, g in sub.groupby("track_id"):
        ax[0].plot(g["hours"], g["length_um"], "-o", ms=2.5, lw=1, alpha=0.8, label=f"{tid}")
    ax[0].set_xlabel("time (h)"); ax[0].set_ylabel("skeleton length (um)")
    ax[0].set_title(f"PROPOSED per-object growth ({len(keep)} tracks >= {min_frames} frames)")
    if len(keep) <= 12:
        ax[0].legend(fontsize=7, ncol=2, title="track")
    # growth rate: linear fit per track (um/min) using the real time axis
    rates = []
    for tid, g in sub.groupby("track_id"):
        if len(g) >= max(3, min_frames) and g["hours"].nunique() > 1:
            slope = np.polyfit(g["hours"] * 60.0, g["length_um"], 1)[0]  # um per minute
            rates.append({"track_id": tid, "frames": len(g), "um_per_min": round(float(slope), 4),
                          "start_um": float(g["length_um"].iloc[0]),
                          "end_um": float(g["length_um"].iloc[-1])})
    rdf = pd.DataFrame(rates)
    if not rdf.empty:
        atomic_write_text(str(Path(resolved) / "hyphae2d_growth_rates.csv"),
                          rdf.sort_values("um_per_min", ascending=False).to_csv(index=False))
        ax[1].hist(rdf["um_per_min"], bins=20, color="tab:blue", alpha=0.8)
        ax[1].axvline(0, color="k", lw=1, ls="--")
        ax[1].set_xlabel("growth rate (um/min)"); ax[1].set_ylabel("tracks")
        ax[1].set_title("PROPOSED per-object elongation rate")
    for a in ax: a.grid(alpha=0.3)
    fig.tight_layout()
    png = Path(resolved) / "qc" / "hyphae2d_tracks.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=120, format="png"); plt.close(fig)
    write_done(str(png))
    return str(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Link 2D hyphal objects across time -> per-object growth trajectories (PROPOSED).")
    ap.add_argument("--dataset", default="ROI2", choices=["ROI2", "ROI7"])
    ap.add_argument("--min-iou", type=float, default=0.1, dest="min_iou",
                    help="Minimum IoU with the previous frame to continue a track.")
    ap.add_argument("--min-frames", type=int, default=3, dest="min_frames",
                    help="Only plot/fit tracks seen in at least this many frames.")
    ap.add_argument("--t-start", type=int, default=0, dest="t_start")
    ap.add_argument("--t-stop", type=int, default=120, dest="t_stop")
    ap.add_argument("--out-dir", default="results/{roi}", dest="out_dir")
    args = ap.parse_args()

    out = finalize(args.dataset, args.out_dir, min_frames=args.min_frames,
                   t_start=args.t_start, t_stop=args.t_stop, min_iou=args.min_iou)
    if not out:
        return
    import pandas as pd
    df = pd.read_csv(out)
    n_tracks = df["track_id"].nunique()
    lengths = df.groupby("track_id").size()
    print(f"wrote {out}: {n_tracks} tracks over {df['t'].nunique()} frames "
          f"(median {int(lengths.median())} frames/track, longest {int(lengths.max())})")
    print(f"events: {df['event'].value_counts().to_dict()}")
    print("PROPOSED — confirm the per-frame masks before trusting any growth rate.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
