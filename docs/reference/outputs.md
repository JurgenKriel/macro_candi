# Outputs reference

Everything is written under `results/{DATASET}/`. Each file is written atomically and has a
`.done` sentinel next to it once it is complete. A file **without** its `.done` is
incomplete; delete it or re-run the stage.

## Files

| File | Stage | Contents |
|---|---|---|
| `calibration.json` | 3 | Voxel size (µm), isotropy check, per-timepoint timestamps (s) |
| `bleach.json` | 3 | Per-(t, channel) correction factors, method, reference frame, `apply` flag + reason |
| `qc/bleach_decay_{channel}.png` | 3 | Raw vs corrected foreground mean over time |
| `{channel}_labels.zarr/t{NNN}` | 5 | 3D `uint16` instance labels, one array per timepoint |
| `census.csv` | 5 | Objects per frame per channel |
| `engulfment/t{NNN}.json` | 6 | Per-timepoint shard: class counts + per-object rows |
| `engulfment.csv` | 6 | One row per (t, *Candida* object) |
| `{channel}_mip.zarr` | 7 | Z max-projection timelapse |
| `candida_mip_labels.zarr` | 7 | 2D labels per frame |
| `hyphae2d_objects.csv` | 7 | Skeleton metrics per (t, object) |
| `hyphae2d_frame.csv` | 7 | Per-frame aggregates |
| `hyphae2d_tracks.csv` | 7 | Objects linked across frames |
| `hyphae2d_growth_rates.csv` | 7 | Elongation rate per track |
| `qc/*.png` | all | QC images |

## Columns

### `census.csv`

| Column | Type | Meaning |
|---|---|---|
| `dataset` | str | Dataset id |
| `channel` | str | `macrophage` or `candida` |
| `t` | int | Timepoint index (0-based) |
| `n_objects` | int | Number of labels in the frame |
| `fg_voxels` | int | Total labelled voxels |

### `engulfment.csv`

| Column | Type | Meaning |
|---|---|---|
| `dataset`, `t` | str, int | Dataset and timepoint |
| `candida` | int | *Candida* label id in this frame (**not** stable across frames) |
| `candida_vox` | int | Object volume in voxels (× 0.145³ µm³ for the reference data) |
| `best_macrophage` | int | Macrophage with the largest overlap; `0` = none |
| `overlap_frac` | float | Fraction of the object inside `best_macrophage` (0–1) |
| `PROPOSED_class` | str | `engulfed` / `partial` / `free` |

### `hyphae2d_objects.csv`

| Column | Unit | Meaning |
|---|---|---|
| `dataset`, `t`, `obj` | – | Dataset, timepoint, per-frame object id |
| `length_um` | µm | Total skeleton length of the object |
| `n_branches` | – | Skeleton branches |
| `n_tips` | – | Degree-1 nodes (growing ends) |
| `n_junctions` | – | Nodes of degree > 2 (branch points) |
| `longest_branch_um` | µm | Longest single branch |
| `area_um2` | µm² | Projected mask area |
| `hours` | h | Real elapsed time of this frame |

### `hyphae2d_tracks.csv`

| Column | Meaning |
|---|---|
| `dataset`, `t`, `obj` | Object in a frame (joins to `hyphae2d_objects.csv`) |
| `track_id` | Stable id across frames |
| `event` | Empty, `merge` (two tracks joined) or `split` |

### `hyphae2d_growth_rates.csv`

| Column | Unit | Meaning |
|---|---|---|
| `track_id` | – | Track |
| `frames` | – | Number of frames fitted |
| `um_per_min` | µm/min | Slope of length vs real time |
| `start_um`, `end_um` | µm | Length at first and last fitted frame |

## Loading results

```python
import pandas as pd

eng = pd.read_csv("results/ROI2/engulfment.csv")
eng.groupby(["t", "PROPOSED_class"]).size().unstack(fill_value=0).plot()

objs   = pd.read_csv("results/ROI2/hyphae2d_objects.csv")
tracks = pd.read_csv("results/ROI2/hyphae2d_tracks.csv")
per_track = objs.merge(tracks, on=["dataset", "t", "obj"])
```

```python
import zarr
labels = zarr.open_group("results/ROI2/macrophage_labels.zarr", mode="r")["t040"][:]
```
