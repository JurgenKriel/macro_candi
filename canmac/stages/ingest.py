"""Phase-1 ingest orchestrator — emit all sidecars for both datasets (D-07/D-08).

For each of the EXACTLY 2 manifest datasets (ROI2, ROI7 — D-01; this loops
datasets and timepoints, never the buggy per-FOV subunit count the parent
pipeline miscounted — D-02) this stage:

1. emits ``results/{roi}/calibration.json`` (:func:`canmac.stages.calibrate.emit_calibration`);
2. per channel (candida, macrophage) computes per-(t,channel) foreground bleach
   factors (:func:`canmac.io.bleach.compute_bleach_factors`) and stores them to
   ``results/{roi}/bleach.json`` (:func:`canmac.io.bleach.save_bleach`) — the
   ~32 GB ``raw.zarr`` is never rewritten (D-07);
3. emits a raw-vs-corrected QC decay plot ``results/{roi}/qc/bleach_decay_{channel}.png``
   (raw foreground-mean ``S[t]`` vs corrected ``S[t]*f[t]`` on the real DeltaT
   time axis; title carries the residual-slope ratio — D-08), then a ``.done``
   sentinel written LAST.

Runs matplotlib on the ``Agg`` backend (headless SLURM node). Streams one raw
``(Z,Y,X)`` volume per timepoint through :func:`canmac.io.reader.get_view`
(``correct=False``) — the 4D array is never materialized.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless — no display on SLURM compute nodes
import matplotlib.pyplot as plt  # noqa: E402

from canmac.io.atomic import write_done  # noqa: E402
from canmac.io.bleach import (  # noqa: E402
    CHANNELS,
    apply_reason,
    compute_bleach_factors,
    linear_slope,
    save_bleach,
    should_apply,
)
from canmac.io.manifest import datasets  # noqa: E402
from canmac.io.metadata import timestamps_s  # noqa: E402
from canmac.stages.calibrate import emit_calibration  # noqa: E402


def _paths_for(record: dict) -> tuple[str, str]:
    """Resolve the (.zattrs, OME XML) paths for a validated manifest record."""
    store = Path(record["_store"])
    return str(store / "0" / ".zattrs"), str(store / "OME" / "METADATA.ome.xml")


def _slope_ratio(S: list[float], factors: dict[int, float], x: list[float]) -> float:
    """Residual-slope ratio |slope(corrected)| / |slope(raw)| over the time axis ``x``."""
    corrected = [S[t] * factors[t] for t in range(len(S))]
    raw_slope = abs(linear_slope(S, x))
    corr_slope = abs(linear_slope(corrected, x))
    return (corr_slope / raw_slope) if raw_slope > 0 else 0.0


def emit_qc_plot(
    dataset: str,
    channel: str,
    S: list[float],
    factors: dict[int, float],
    timestamps: list[float],
    applied: bool = True,
    out_dir: str = "results/{roi}",
) -> str:
    """Write the QC decay PNG (+ ``.done`` last) for one channel.

    The x-axis is the real DeltaT time span in hours (D-06/D-08) — never a 0..119
    frame index. For an ``applied`` (macrophage) channel this is a raw-vs-corrected
    plot: the raw foreground mean photobleaches and the corrected ``S[t]*f[t]``
    curve is flat (title carries the residual-slope ratio). For a NOT-``applied``
    channel (candida) the correction is intentionally withheld — the raw curve is
    real biological growth, so the would-be correction is drawn as a faint dashed
    line explicitly labelled "NOT applied" and a caption explains why, so the plot
    is never misread as a failed correction (01-04 checkpoint decision).
    """
    hours = [ts / 3600.0 for ts in timestamps]
    corrected = [S[t] * factors[t] for t in range(len(S))]
    ratio = _slope_ratio(S, factors, hours)

    qc_dir = Path(out_dir.format(roi=dataset)) / "qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    png = qc_dir / f"bleach_decay_{channel}.png"

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.set_xlabel("time (h, from OME DeltaT)")
    ax.set_ylabel("foreground mean intensity")
    ax.grid(True, alpha=0.3)

    if applied:
        ax.plot(hours, S, "o-", ms=3, lw=1, color="tab:red", label="raw foreground mean S[t]")
        ax.plot(
            hours, corrected, "s-", ms=3, lw=1, color="tab:green",
            label="corrected S[t]*f[t] (APPLIED on read)",
        )
        ax.set_title(
            f"{dataset} / {channel} photobleaching correction (APPLIED)\n"
            f"residual slope ratio = {ratio:.3f} (target < 0.25)"
        )
    else:
        ax.plot(
            hours, S, "o-", ms=3, lw=1, color="tab:red",
            label="raw foreground mean S[t] (uncorrected — real growth)",
        )
        ax.plot(
            hours, corrected, "--", lw=1, color="0.6",
            label="ratio correction (NOT applied — would suppress growth)",
        )
        ax.set_title(
            f"{dataset} / {channel}: correction NOT applied\n"
            "rising foreground = biological growth, not photobleaching"
        )
        ax.text(
            0.5, -0.28,
            "Correction intentionally withheld (01-04 decision): the candida foreground mean rises\n"
            "over the timelapse (proliferation / hyphal biomass). A ratio-to-reference correction\n"
            "would divide this real growth signal down to the near-empty t0 level, so it is NOT applied.",
            transform=ax.transAxes, ha="center", va="top", fontsize=7, color="0.25",
        )

    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()

    tmp = png.with_name(f"{png.name}.tmp.{os.getpid()}")
    # Explicit format: the temp name's ".tmp.<pid>" suffix is not a valid
    # extension for matplotlib's format inference (Rule 1 fix).
    fig.savefig(tmp, dpi=120, format="png")
    plt.close(fig)
    os.replace(tmp, png)  # atomic rename into place
    write_done(str(png))  # sentinel LAST (T-01-04-03)
    return str(png)


def process_dataset(record: dict, out_dir: str = "results/{roi}") -> None:
    """Emit calibration + bleach factors + QC plots for ONE dataset."""
    dataset = record["id"]
    zattrs_path, ome_xml_path = _paths_for(record)

    cal_path = emit_calibration(dataset, zattrs_path, ome_xml_path, out_dir=out_dir)
    print(f"[{dataset}] calibration -> {cal_path}")

    timestamps = timestamps_s(ome_xml_path)["timestamps_s"]

    for channel in CHANNELS:
        res: dict[str, Any] = compute_bleach_factors(dataset, channel)
        factors = res["factors"]
        S_raw = res["S_raw"]
        applied = should_apply(channel)  # 01-04 policy: macrophage True, candida False
        save_bleach(
            dataset,
            channel,
            factors,
            S_raw,
            method=res["method"],
            ref=res["ref"],
            apply=applied,
            reason=apply_reason(channel),
            out_dir=out_dir,
        )
        S = [S_raw[t] for t in range(res["n_t"])]
        png = emit_qc_plot(dataset, channel, S, factors, timestamps, applied=applied, out_dir=out_dir)
        hours = [ts / 3600.0 for ts in timestamps]
        ratio = _slope_ratio(S, factors, hours)
        print(
            f"[{dataset}/{channel}] method={res['method']} ref={res['ref']} "
            f"apply={applied} n_t={res['n_t']} residual_slope_ratio={ratio:.3f} -> {png}"
        )


def main() -> None:
    """Emit all Phase-1 sidecars for every dataset enumerated in the manifest."""
    parser = argparse.ArgumentParser(
        description="Phase-1 ingest: emit calibration + bleach factors + QC plots."
    )
    parser.add_argument(
        "--manifest", default=None, help="Path to manifest.yaml (default: repo-root manifest.yaml)."
    )
    parser.add_argument(
        "--out-dir", default="results/{roi}", help="Output dir template (default: results/{roi})."
    )
    args = parser.parse_args()

    for record in datasets(args.manifest):  # loops the 2 datasets — NEVER scenes (D-02)
        process_dataset(record, out_dir=args.out_dir)


if __name__ == "__main__":
    main()
