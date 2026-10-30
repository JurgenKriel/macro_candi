# Command-line reference

Run every command from the repository root. Python stages are invoked as
`pixi run python -m canmac.stages.<name>`. Add `--help` to any command for its built-in help.

!!! note "Options shared by every stage and viewer"
    | Flag | Default | |
    |---|---|---|
    | `--dataset ID` | the manifest's only dataset | Any id from the manifest. Required when it lists several. |
    | `--manifest PATH` | `$CANMAC_MANIFEST`, else repo-root `manifest.yaml` | Which manifest to read |

    `view_labels.py` takes the dataset id as its first positional argument and reads the
    manifest from `CANMAC_MANIFEST`.

## Pipeline stages

### `canmac.stages.ingest`

Calibration + bleach factors + QC plots for every dataset in the manifest.

| Flag | Default | |
|---|---|---|
| `--manifest PATH` | `$CANMAC_MANIFEST`, else repo-root `manifest.yaml` | Manifest to read |
| `--out-dir TEMPLATE` | `results/{roi}` | Output directory; `{roi}` is replaced by the dataset id |

### `canmac.stages.segment`

| Flag | Default | |
|---|---|---|
| `--channel` | – | `macrophage` / `candida` |
| `--t N` | – | Timepoint (the SLURM script passes `$SLURM_ARRAY_TASK_ID`) |
| `--params PATH` | `params/cpsam_{channel}.json` | Segmentation params (`"method": "classical"` = CPU demo backend) |
| `--finalize` | – | Combine census shards into `census.csv` and exit |
| `--out-dir TEMPLATE` | `results/{roi}` | Output directory |

### `canmac.stages.engulfment`

| Flag | Default | |
|---|---|---|
| `--t N` / `--all-t` | – | One timepoint, or every timepoint (`dims.T`) |
| `--engulf-frac F` | `0.9` | Threshold for `engulfed` |
| `--touch-frac F` | `0.05` | Threshold for `partial` |
| `--dilate N` | `0` | Grow macrophages by N voxels before testing |
| `--candida-store NAME` | – | Alternative *Candida* label store |
| `--finalize` | – | Combine shards into `engulfment.csv` |
| `--out-dir TEMPLATE` | `results/{roi}` | |

### `canmac.stages.hyphae2d`

| Flag | Default | |
|---|---|---|
| `--build-mip` | – | Build the Z-projection timelapse |
| `--t N` / `--all-t` | – | One timepoint, or all in `[--t-start, --t-stop)` |
| `--t-start`, `--t-stop` | `0`, `dims.T` | Timepoint range |
| `--method` | `cellsam` | `cellsam` (GPU) or `threshold` (CPU) |
| `--normalize` | `localstd` | `none` / `localstd` / `clahe` |
| `--norm-sigma` | `12.0` | Scale for local normalisation |
| `--norm-mask-percentile` | `98.0` | |
| `--min-size` | `30` | Minimum object size (px) |
| `--closing` | `2` | Closing radius (px) |
| `--no-qc` | – | Skip QC PNGs |
| `--finalize` | – | Write CSVs and growth-curve plot |
| `--out-dir TEMPLATE` | `results/{roi}` | |

### `canmac.stages.hyphae_track`

| Flag | Default | |
|---|---|---|
| `--min-iou F` | `0.1` | Minimum overlap to link objects |
| `--min-frames N` | `3` | Minimum track length for a rate fit |
| `--t-start`, `--t-stop` | `0`, `dims.T` | |
| `--out-dir TEMPLATE` | `results/{roi}` | |

### `canmac.demo`

| Command | |
|---|---|
| `python -m canmac.demo generate [--out data/demo] [--force]` | Write the synthetic dataset, its manifest and `ground_truth.json` |
| `python -m canmac.demo check [--out data/demo] [--results results/DEMO]` | Score results against the ground truth; exit 1 on failure |

See [Try the demo](../getting-started/demo.md).

## SLURM scripts

All source `config.sh`, `cd` to the repo, and log to `logs/`.

| Script | Inputs | Default resources |
|---|---|---|
| `scripts/slurm/00_env_smoke.sh` | – | 15 min |
| `scripts/slurm/20_segment_3d.sh` | env `DATASET`, `CHANNEL` | array `0-119`, 8 h per task. For other T, pass `--array=0-<T-1>` |
| `scripts/slurm/30_hyphae2d.sh` | env `DATASET` | 2 h |
| `scripts/slurm/40_engulfment.sh` | env `DATASET` | 8 CPU, 64 GB, 4 h |
| `scripts/conversion/01_extract_metadata.sh` | `<czi> <out_dir> <extract_metadata.py>` | |
| `scripts/conversion/02_convert_czi_to_zarr.sh` | `<czi> <out_dir>` | 48 CPU, 256 GB, 24 h |
| `scripts/conversion/10_zarr_to_ometiff.sh` | `<raw.zarr> <out.ome.tiff>` | |
| `scripts/demo/run_demo.sh` | – | Whole pipeline on the synthetic demo, CPU only |

Pass partition, GPU and environment on the `sbatch` line, e.g.
`sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES --export=ALL,DATASET=ROI2 …`.

## Cropping scripts

| Script | Writes? | Display? | Purpose |
|---|---|---|---|
| `scripts/cropping/11_preview_crop.py T` | never | yes (or `--check`) | Look at a box in napari; press ++j++ / ++k++ on opposite corners to draw the box and print crop flags |
| `scripts/cropping/12_crop_roi.py --out PATH` | `--out` | no | Copy the box (and `--t0`..`--t1`) to a Blender-ready OME-Zarr |

Both take `--x0/--x1/--y0/--y1/--z0/--z1` in **level-0 voxels** (stop excluded), plus
`--channels`, `--include-lysed`, `--level` and `--store` (default `$CANMAC_FULLSTACK_ZARR`, else
`full_stack/raw.zarr`; relative paths resolve against your current directory). The cropper also
takes `--t0/--t1`, `--extra-levels`, `--chunk-xy`, `--chunk-z`, `--dry-run` and `--overwrite`.
Full option tables, a walkthrough and troubleshooting:
[Stage 4 · Cropping & 3D rendering](../pipeline/04-cropping.md#option-reference).

## Viewers

See [Review & QC](../review.md#napari-viewers).
