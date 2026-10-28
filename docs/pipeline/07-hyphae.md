# 7 · Hyphal growth (2D MIP)

<div class="mc-meta" markdown>
<span>**Runs on** GPU · one job (model loaded once)</span>
<span>**Time** ~1–2 h</span>
<span>**Needs** Stage 3 calibration</span>
</div>

## What it does

Hyphae are quantified as a **network** (a skeleton graph), never as separate blobs.
Instance segmenters don't represent long, branched filaments well.

```mermaid
flowchart LR
    v["3D frame"] -- "max over Z" --> m["2D MIP"]
    m -- "Cellpose-SAM 2D" --> l["object masks"]
    l -- "skeletonize" --> s["skeleton graph<br/><small>length · tips · junctions</small>"]
    s -- "IoU link t → t+1" --> t["tracks<br/><small>µm/min per hypha</small>"]
```

The SLURM script runs four steps in order:

1. **`--build-mip`**: Z max-projects every timepoint once into `{channel}_mip.zarr`.
2. **`--all-t --method cellsam`**: segments each 2D frame, skeletonises, and measures **per
   object**: length, branches, tips (degree-1 nodes), junctions (degree > 2), longest branch,
   area.
3. **`--finalize`**: writes `hyphae2d_objects.csv`, `hyphae2d_frame.csv` and a growth-curve plot.
4. **`hyphae_track`**: links objects **across frames by overlap (IoU)** into tracks and fits
   per-track elongation rates in µm/min on the **real** time axis.

### Why link by overlap?

Hyphae stay in place and grow by extending, so at a 3-minute interval a filament overlaps
its own previous mask heavily. A growing filament's *centroid* moves as it extends, which
confuses distance-based trackers. Overlap linking avoids that.

Merges (two tracks join, e.g. anastomosis or crossing in projection) and splits are
**recorded** in an `event` column, not hidden.

!!! note "Not for macrophages"
    Overlap linking assumes objects that barely move. Macrophages crawl, so track them with a
    dedicated tracker (e.g. ultrack, btrack).

## Run it

```bash
sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES \
       --export=ALL,DATASET=ROI2 scripts/slurm/30_hyphae2d.sh
```

To try settings on one frame without a GPU queue:

```bash
pixi run python -m canmac.stages.hyphae2d --dataset ROI2 --t 60 --method threshold
```

### Key options

| Flag | Default | Meaning |
|---|---|---|
| `--method` | `cellsam` | `cellsam` (GPU) or `threshold` (CPU, quick look) |
| `--normalize` | `localstd` | Contrast flattening before segmentation: `none`, `localstd`, `clahe` |
| `--min-size` | `30` | Drop objects smaller than this (pixels) |
| `--closing` | `2` | Morphological closing radius to bridge small gaps |
| `hyphae_track --min-iou` | `0.1` | Minimum overlap to link two objects |
| `hyphae_track --min-frames` | `3` (script uses `5`) | Minimum track length before a growth rate is fitted |

## What it writes

| File | Contents |
|---|---|
| `{channel}_mip.zarr` | Z-projection timelapse |
| `candida_mip_labels.zarr` | 2D labels per frame (recompute metrics without a GPU) |
| `hyphae2d_objects.csv` | Per (t, object): `length_um`, `n_branches`, `n_tips`, `n_junctions`, `longest_branch_um`, `area_um2`, `hours` |
| `hyphae2d_frame.csv` | Per frame: object count, total/mean/median/max length, tips, … |
| `hyphae2d_tracks.csv` | `dataset, t, obj, track_id, event` |
| `hyphae2d_growth_rates.csv` | Per track: `frames`, `um_per_min`, `start_um`, `end_um` |
| `qc/` | Overlay PNG per timepoint, growth curves |

## Check it

- [ ] Open `notebooks/04_hyphae2d_review.ipynb`: per-timepoint panels next to their metrics,
      growth curves, and a labelled montage.
- [ ] Do the skeletons follow the hyphae, without spurs or breaks along dim stretches?
- [ ] Are tracks long? Short tracks mean masks are unstable from frame to frame.
- [ ] **Negative growth rates** are a red flag. Hyphae don't shrink, so a negative rate
      points to a segmentation problem, not biology.

!!! info "Lengths are a lower bound"
    Filaments that cross in depth merge in a 2D projection, so measured length ≤ true 3D
    length.
