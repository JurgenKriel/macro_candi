"""Phase-6 proof of concept — 2D MIP hyphal network growth over the ROI2 timelapse.

Per timepoint: max-intensity-project the candida channel over Z, segment the hyphal network
in 2D, skeletonize, and measure the skeleton graph with skan. Emits a per-timepoint metrics
row + a QC overlay PNG, then a growth curve over time.

This is the CLAUDE.md-prescribed treatment of hyphae: quantify them as a **network**
(total length, tips, junctions), never as instances. Working on the 2D MIP makes the PoC cheap
and CPU-only (no GPU contention with the 3D segmentation arrays) and is the standard way to
track filament growth; the tradeoff is that overlapping filaments merge in projection, so
lengths are a LOWER BOUND on the true 3D network.

**All output is PROPOSED** — the growth curve is only meaningful if the per-timepoint skeletons
actually trace the hyphae, which is a visual judgement (QC PNGs are written for exactly that).

Metrics per timepoint (physical units, 0.145 um/px):
  * ``total_length_um``  — summed skeleton branch length (the primary growth readout)
  * ``n_branches``       — skan branch count
  * ``n_tips``           — skeleton nodes of degree 1 (growing ends)
  * ``n_junctions``      — nodes of degree > 2 (branch points; loops => anastomosis)
  * ``longest_branch_um``, ``mean_branch_um``, ``fg_area_um2``, ``n_components``
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Optional

import numpy as np

from canmac.io.atomic import atomic_write_json, atomic_write_text, write_done

_LOG = logging.getLogger(__name__)


def mip(dataset: str, channel: str, t: int) -> np.ndarray:
    """Max-intensity projection over Z of ONE timepoint (streamed; never the 4D array)."""
    from canmac.io.reader import get_view

    correct = channel == "macrophage"  # D-05: candida read RAW (its rise is growth)
    vol = get_view(dataset, channel, t, correct=correct)
    return np.ascontiguousarray(vol.max(axis=0).compute()).astype(np.float32)


def build_mip(dataset: str, channel: str = "candida", t_start: int = 0, t_stop: int = 120,
              out_dir: str = "results/{roi}") -> str:
    """Build the MIP timelapse ONCE as a saved artifact: ``{out_dir}/{channel}_mip.zarr``.

    One ``t{NNN}`` 2D array per timepoint (atomic, ``.done`` last, restart-safe). Computing the
    projections once means the segmentation pass reads small 2D frames instead of re-streaming
    301-slice volumes for every experiment.
    """
    import zarr

    from canmac.io.atomic import is_done, write_done

    resolved = out_dir.format(roi=dataset) if "{roi}" in out_dir else out_dir
    store = f"{resolved}/{channel}_mip.zarr"
    root = zarr.open_group(store, mode="a")
    for t in range(t_start, t_stop):
        marker = f"{store}/t{t:03d}"
        if is_done(marker):
            continue
        img = mip(dataset, channel, t)
        arr = root.create_array(f"t{t:03d}", shape=img.shape, dtype="float32",
                                chunks=img.shape, overwrite=True)
        arr[:] = img
        write_done(marker)
    return store


def load_mip(dataset: str, t: int, channel: str = "candida", out_dir: str = "results/{roi}"):
    """Read one saved MIP frame (or None if not built yet)."""
    import zarr

    resolved = out_dir.format(roi=dataset) if "{roi}" in out_dir else out_dir
    store = f"{resolved}/{channel}_mip.zarr"
    if not Path(store).exists():
        return None
    root = zarr.open_group(store, mode="r")
    key = f"t{t:03d}"
    return root[key][:] if key in root else None


_CP_MODEL = None


def segment_2d_cellsam(img: np.ndarray, params: Optional[dict] = None) -> np.ndarray:
    """Segment a 2D MIP frame with **Cellpose-SAM** -> instance labels (GPU if available).

    2D path: ``do_3D=False`` and NO ``z_axis`` (the frame is (Y,X)); ``diameter=None`` keeps
    Cellpose 4's size-invariance. The model is cached across frames so a whole timelapse pays
    the weight load once. Live knobs are ``cellprob_threshold`` (lower -> more/larger objects)
    and ``flow_threshold`` (which, unlike the 3D path, IS active in 2D).
    """
    global _CP_MODEL
    params = params or {}
    if _CP_MODEL is None:
        import torch
        from cellpose import models

        gpu = torch.cuda.is_available()
        if gpu:
            dev = torch.cuda.get_device_name(0)
            assert "P100" not in dev, f"refusing {dev} (P100 OOMs cpsam) — request an A30/A100"
            _LOG.info("cellsam 2D on GPU: %s", dev)
        else:
            _LOG.warning("no CUDA — running Cellpose-SAM 2D on CPU (slower but workable in 2D)")
        from canmac.stages.segment import WEIGHTS

        _CP_MODEL = models.CellposeModel(gpu=gpu, pretrained_model=WEIGHTS)
    labels, _flows, _styles = _CP_MODEL.eval(
        np.ascontiguousarray(img).astype(np.float32),
        do_3D=False,                 # 2D frame
        channel_axis=None,
        cellprob_threshold=params.get("cellprob_threshold", 0.0),
        flow_threshold=params.get("flow_threshold", 0.4),   # ACTIVE in 2D (inert in 3D)
        min_size=params.get("min_size", 15),
        normalize=True,
    )
    return labels.astype(np.uint16)


def segment_2d(
    img: np.ndarray,
    normalize: str = "localstd",
    norm_sigma: float = 12.0,
    norm_mask_percentile: Optional[float] = 98.0,
    min_size: int = 30,
    closing: int = 2,
) -> np.ndarray:
    """Segment the hyphal network in a 2D MIP -> boolean mask.

    Local-contrast normalization first (the dim-stretch fix — a global threshold otherwise cuts
    filaments where they fade), then Otsu, small-object removal, and a light binary closing to
    bridge 1-2 px gaps so a single filament doesn't skeletonize into fragments.
    """
    from skimage.filters import threshold_otsu
    from skimage.morphology import binary_closing, disk, remove_small_objects

    from canmac.stages.segment import normalize_volume

    work = img
    if normalize not in (None, "none"):
        # normalize_volume is dimension-agnostic (Gaussian/CLAHE over whatever axes exist)
        work = normalize_volume(img, normalize, sigma=norm_sigma,
                                mask_percentile=norm_mask_percentile if normalize == "localstd" else None)
    mask = work > threshold_otsu(work)
    if closing > 0:
        mask = binary_closing(mask, disk(closing))
    return remove_small_objects(mask, min_size)


def object_metrics(mask: np.ndarray, vox_um: float = 0.14499219272808386):
    """PER-OBJECT skeleton metrics — one row per connected component (multiple hyphae per frame).

    Skeletonizes once, then attributes every skan branch to its connected component, so each
    hypha/object gets its OWN length, tips, junctions and area. Returns
    ``(per_object_df, frame_summary, skel)``; the frame summary keeps the aggregate (sum) plus
    per-object statistics (mean/median/max length) so a plot can show either.

    NOTE: object ids are per-frame connected-component labels — they are NOT linked across
    timepoints. Following ONE hypha over time needs tracking (Phase 6); until then, per-object
    growth is a distribution per frame, not a trajectory.
    """
    import pandas as pd
    from skimage.measure import label, regionprops
    from skimage.morphology import skeletonize
    from skan import Skeleton, summarize

    cc = label(mask)
    skel = skeletonize(mask)
    rows = []
    per_obj_branches: dict[int, list] = {}
    junc_deg: dict[int, int] = {}
    tips_deg: dict[int, int] = {}
    if skel.sum() >= 2:
        sk = Skeleton(skel, spacing=(vox_um, vox_um))
        df = summarize(sk, separator="_")
        deg = np.asarray(sk.degrees)
        coords = np.asarray(sk.coordinates)          # node coords (px) -> component lookup
        # attribute each skeleton node's degree to its connected component
        for i, (yy, xx) in enumerate(np.round(coords).astype(int)):
            yy = min(max(yy, 0), cc.shape[0] - 1); xx = min(max(xx, 0), cc.shape[1] - 1)
            lab = int(cc[yy, xx])
            if lab == 0:
                continue
            if deg[i] == 1:
                tips_deg[lab] = tips_deg.get(lab, 0) + 1
            elif deg[i] > 2:
                junc_deg[lab] = junc_deg.get(lab, 0) + 1
        # attribute each branch to a component via its source coordinate
        for r in df.itertuples():
            yy = int(round(getattr(r, "image_coord_src_0")))
            xx = int(round(getattr(r, "image_coord_src_1")))
            yy = min(max(yy, 0), cc.shape[0] - 1); xx = min(max(xx, 0), cc.shape[1] - 1)
            lab = int(cc[yy, xx])
            if lab:
                per_obj_branches.setdefault(lab, []).append(float(r.branch_distance))
    for pr in regionprops(cc):
        b = per_obj_branches.get(pr.label, [])
        rows.append({"obj": int(pr.label),
                     "length_um": round(float(sum(b)), 3),
                     "n_branches": len(b),
                     "n_tips": int(tips_deg.get(pr.label, 0)),
                     "n_junctions": int(junc_deg.get(pr.label, 0)),
                     "longest_branch_um": round(float(max(b)), 3) if b else 0.0,
                     "area_um2": round(float(pr.area) * vox_um ** 2, 3)})
    per_obj = pd.DataFrame(rows, columns=["obj","length_um","n_branches","n_tips",
                                          "n_junctions","longest_branch_um","area_um2"])
    if not per_obj.empty:
        per_obj = per_obj.sort_values("length_um", ascending=False).reset_index(drop=True)
    L = per_obj["length_um"] if len(per_obj) else pd.Series(dtype=float)
    summary = {"n_objects": int(len(per_obj)),
               "total_length_um": round(float(L.sum()), 3) if len(L) else 0.0,
               "mean_length_um": round(float(L.mean()), 3) if len(L) else 0.0,
               "median_length_um": round(float(L.median()), 3) if len(L) else 0.0,
               "max_length_um": round(float(L.max()), 3) if len(L) else 0.0,
               "n_tips": int(per_obj["n_tips"].sum()) if len(per_obj) else 0,
               "n_junctions": int(per_obj["n_junctions"].sum()) if len(per_obj) else 0,
               "fg_area_um2": round(float(per_obj["area_um2"].sum()), 3) if len(per_obj) else 0.0}
    return per_obj, summary, skel


def skeleton_metrics(mask: np.ndarray, vox_um: float = 0.14499219272808386) -> dict:
    """Skeletonize a 2D mask and measure the network graph (skan) in physical units."""
    from skimage.measure import label
    from skimage.morphology import skeletonize
    from skan import Skeleton, summarize

    skel = skeletonize(mask)
    out = {"total_length_um": 0.0, "n_branches": 0, "n_tips": 0, "n_junctions": 0,
           "longest_branch_um": 0.0, "mean_branch_um": 0.0,
           "fg_area_um2": round(float(mask.sum()) * vox_um ** 2, 3),
           "n_components": int(label(mask).max())}
    if skel.sum() < 2:
        return out, skel
    sk = Skeleton(skel, spacing=(vox_um, vox_um))
    df = summarize(sk, separator="_")
    deg = np.asarray(sk.degrees)
    out.update({
        "total_length_um": round(float(df["branch_distance"].sum()), 3),
        "n_branches": int(len(df)),
        "n_tips": int((deg == 1).sum()),          # growing ends
        "n_junctions": int((deg > 2).sum()),      # branch points
        "longest_branch_um": round(float(df["branch_distance"].max()), 3),
        "mean_branch_um": round(float(df["branch_distance"].mean()), 3),
    })
    return out, skel


def qc_png(img: np.ndarray, mask: np.ndarray, skel: np.ndarray, path: str, title: str) -> None:
    """Write a raw-MIP | mask | skeleton-on-raw QC panel (atomic; .done last)."""
    import os

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(1, 3, figsize=(15, 5))
    ax[0].imshow(img, cmap="gray"); ax[0].set_title(f"{title}\nraw MIP")
    ax[1].imshow(mask, cmap="gray"); ax[1].set_title("2D mask")
    ax[2].imshow(img, cmap="gray")
    ys, xs = np.where(skel)
    ax[2].scatter(xs, ys, s=0.4, c="red"); ax[2].set_title("skeleton on raw")
    for a in ax: a.axis("off")
    fig.tight_layout()
    tmp = f"{path}.tmp.{os.getpid()}"
    fig.savefig(tmp, dpi=110, format="png"); plt.close(fig)
    os.replace(tmp, path)
    write_done(path)


def process_timepoint(dataset: str, t: int, out_dir: str = "results/{roi}",
                      vox_um: float = 0.14499219272808386, qc: bool = True, **seg_kw) -> dict:
    """MIP -> 2D segment -> skeleton metrics for ONE timepoint; writes a shard (+ QC PNG)."""
    resolved = out_dir.format(roi=dataset) if "{roi}" in out_dir else out_dir
    img = load_mip(dataset, t, out_dir=out_dir)      # saved MIP artifact
    if img is None:
        img = mip(dataset, "candida", t)             # fall back to computing it
    method = seg_kw.pop("method", "cellsam")
    if method == "cellsam":
        labels = segment_2d_cellsam(img, seg_kw.pop("params", None))
        mask = labels > 0                            # network view: skeletonize the union
        seg_kw.clear()
    else:
        mask = segment_2d(img, **seg_kw)
    per_obj, summary, skel = object_metrics(mask, vox_um)
    # save the 2D mask labels so per-object metrics can be RE-measured without the GPU
    try:
        import zarr

        from canmac.io.atomic import is_done as _isdone
        from skimage.measure import label as _label
        lstore = f"{resolved}/candida_mip_labels.zarr"
        lroot = zarr.open_group(lstore, mode="a")
        lab = _label(mask).astype("uint16")
        larr = lroot.create_array(f"t{t:03d}", shape=lab.shape, dtype="uint16",
                                  chunks=lab.shape, overwrite=True)
        larr[:] = lab
        write_done(f"{lstore}/t{t:03d}")
    except Exception as e:  # pragma: no cover
        _LOG.warning("could not save 2D labels for t%03d: %s", t, e)
    metrics = {"dataset": dataset, "t": int(t), **summary,
               "objects": per_obj.to_dict("records")}
    shard = Path(resolved) / "hyphae2d" / f"t{t:03d}.json"
    atomic_write_json(shard, metrics)
    write_done(str(shard))
    if qc:
        qc_png(img, mask, skel, str(Path(resolved) / "qc" / f"hyphae2d_t{t:03d}.png"),
               f"{dataset} candida MIP t{t:03d}")
    return metrics


def finalize(dataset: str, out_dir: str = "results/{roi}", timestamps: bool = True) -> str:
    """Concat shards -> ``hyphae2d.csv`` and plot the growth curve (total length vs real time)."""
    import json

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    resolved = out_dir.format(roi=dataset) if "{roi}" in out_dir else out_dir
    rows = []
    d = Path(resolved) / "hyphae2d"
    if d.exists():
        for shard in sorted(d.glob("t*.json")):
            rows.append(json.loads(shard.read_text()))
    obj_rows = []
    for r in rows:
        for o in r.get("objects", []):
            obj_rows.append({"dataset": r["dataset"], "t": r["t"], **o})
    df = pd.DataFrame([{k: v for k, v in r.items() if k != "objects"} for r in rows])
    if df.empty:
        _LOG.warning("no hyphae2d shards found")
        return ""
    df = df.sort_values("t").reset_index(drop=True)
    # real per-timepoint time axis from the OME DeltaT calibration (never an assumed interval)
    if timestamps:
        try:
            from canmac.io.metadata import timestamps_s
            ts = timestamps_s(f"{dataset}/raw.zarr/OME/METADATA.ome.xml")
            vals = next(v for v in ts.values() if isinstance(v, (list, tuple)))
            df["hours"] = [float(vals[int(t)]) / 3600.0 for t in df["t"]]
        except Exception as e:  # pragma: no cover
            _LOG.warning("timestamps unavailable (%s) — falling back to frame index", e)
            df["hours"] = df["t"].astype(float)
    out_csv = Path(resolved) / "hyphae2d_frame.csv"      # one row per frame (aggregate)
    atomic_write_text(str(out_csv), df.to_csv(index=False))
    odf = pd.DataFrame(obj_rows)
    if not odf.empty:
        odf = odf.sort_values(["t", "length_um"], ascending=[True, False]).reset_index(drop=True)
        odf = odf.merge(df[["t", "hours"]], on="t", how="left")
    atomic_write_text(str(Path(resolved) / "hyphae2d_objects.csv"),   # one row per (t, object)
                      odf.to_csv(index=False))

    fig, ax = plt.subplots(1, 3, figsize=(16, 4.5))
    ax[0].plot(df["hours"], df["total_length_um"], "o-", ms=3, color="tab:red", label="total (sum)")
    ax[0].plot(df["hours"], df["max_length_um"], "^-", ms=3, color="tab:orange", label="longest object")
    ax[0].plot(df["hours"], df["mean_length_um"], "s-", ms=3, color="tab:blue", label="mean/object")
    ax[0].set_xlabel("time (h)"); ax[0].set_ylabel("skeleton length (um)"); ax[0].legend(fontsize=8)
    ax[0].set_title("PROPOSED hyphal growth (per-object vs total)")
    ax[1].plot(df["hours"], df["n_tips"], "o-", ms=3, label="tips")
    ax[1].plot(df["hours"], df["n_junctions"], "s-", ms=3, label="junctions")
    ax[1].set_xlabel("time (h)"); ax[1].set_ylabel("count"); ax[1].legend()
    ax[1].set_title("tips / branch points")
    ax[2].plot(df["hours"], df["fg_area_um2"], "o-", ms=3, color="tab:green")
    ax[2].set_xlabel("time (h)"); ax[2].set_ylabel("mask area (um^2)")
    ax[2].set_title("foreground area")
    for a in ax: a.grid(alpha=0.3)
    fig.tight_layout()
    png = Path(resolved) / "qc" / "hyphae2d_growth.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=120, format="png"); plt.close(fig)
    write_done(str(png))
    return str(out_csv)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Phase-6 PoC: 2D MIP hyphal skeleton metrics over the timelapse (PROPOSED).")
    ap.add_argument("--dataset", default="ROI2", choices=["ROI2", "ROI7"])
    ap.add_argument("--t", type=int, default=None, help="Single timepoint.")
    ap.add_argument("--t-start", type=int, default=0, dest="t_start")
    ap.add_argument("--t-stop", type=int, default=120, dest="t_stop")
    ap.add_argument("--all-t", action="store_true", dest="all_t",
                    help="Sweep --t-start..--t-stop (default full 120).")
    ap.add_argument("--normalize", default="localstd", choices=["none", "localstd", "clahe"])
    ap.add_argument("--norm-sigma", type=float, default=12.0, dest="norm_sigma")
    ap.add_argument("--norm-mask-percentile", type=float, default=98.0, dest="norm_mask_percentile")
    ap.add_argument("--min-size", type=int, default=30, dest="min_size")
    ap.add_argument("--closing", type=int, default=2)
    ap.add_argument("--no-qc", action="store_true", dest="no_qc")
    ap.add_argument("--out-dir", default="results/{roi}", dest="out_dir")
    ap.add_argument("--build-mip", action="store_true", dest="build_mip",
                    help="Build the MIP timelapse artifact first, then exit.")
    ap.add_argument("--method", default="cellsam", choices=["cellsam", "threshold"],
                    help="cellsam = Cellpose-SAM 2D per frame (default); threshold = Otsu.")
    ap.add_argument("--finalize", action="store_true")
    args = ap.parse_args()

    if args.build_mip:
        store = build_mip(args.dataset, "candida", args.t_start, args.t_stop, args.out_dir)
        print("built MIP timelapse ->", store)
        return
    if args.finalize:
        print("wrote", finalize(args.dataset, args.out_dir))
        return

    seg_kw = dict(method=args.method, params=None) if args.method == "cellsam" else dict(
        normalize=args.normalize, norm_sigma=args.norm_sigma,
                  norm_mask_percentile=args.norm_mask_percentile,
                  min_size=args.min_size, closing=args.closing)
    if args.method == "cellsam":
        import json
        pth = "params/cpsam_candida.json"
        seg_kw["params"] = json.load(open(pth)) if Path(pth).exists() else None
    ts = range(args.t_start, args.t_stop) if args.all_t else ([args.t] if args.t is not None else [])
    if not ts:
        ap.error("give --t N, or --all-t, or --finalize")
    for t in ts:
        m = process_timepoint(args.dataset, t, args.out_dir, qc=not args.no_qc, **seg_kw)
        print(f"t{t:03d}: {m['n_objects']} objects | total={m['total_length_um']:.1f}um "
              f"mean/obj={m['mean_length_um']:.1f}um longest={m['max_length_um']:.1f}um "
              f"tips={m['n_tips']} junctions={m['n_junctions']}")
    print("PROPOSED — confirm the skeletons trace real hyphae in results/{roi}/qc/hyphae2d_t*.png")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
