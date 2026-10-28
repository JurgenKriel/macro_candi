# 5 · 3D segmentation

<div class="mc-meta" markdown>
<span>**Runs on** GPU ≥ 16 GB · SLURM array, 1 task per timepoint</span>
<span>**Time** hours, depending on the queue</span>
<span>**Model** Cellpose-SAM (`cpsam`)</span>
</div>

## What it does

Outlines every macrophage and every *Candida* object in 3D, giving each a unique integer
label, for every timepoint and both channels.

It uses Cellpose-SAM in native 3D (`do_3D=True`) with settings chosen for this data:

| Setting | Value | Why |
|---|---|---|
| `z_axis` | `0` | Required: with Y > Z, Cellpose would otherwise guess the wrong axis and return garbage |
| `anisotropy` | `1.0` | Voxels are isotropic |
| `diameter` | `None` | Cellpose 4 is size-invariant |
| Input dtype | cast to native `float32` | The store is big-endian `>u2`; passing it straight to torch gives garbage masks |

## Tune before you run

Segmenting 120 timepoints costs GPU hours, so tune on a few frames first.

1. Open **`notebooks/03_tune_cellpose.ipynb`**.
2. Edit the params dict, re-run, and inspect the overlay. Try an **early, a middle and a
   late** timepoint, since cells look different as the movie goes on.
3. Save. The notebook writes `params/cpsam_{channel}.json`, the same file the batch job reads.

Or tune from the viewer:

```bash
pixi run python viewers/view_labels.py ROI2 macrophage 60 --segment --cellprob 0.0 --min-size 15
pixi run python viewers/view_labels.py ROI2 macrophage 60 --segment --cellprob 0.0 --min-size 15 --save-params
```

### Parameters

| Key | Effect | Shipped value (macrophage / candida) |
|---|---|---|
| `cellprob_threshold` | Higher → fewer, tighter objects | `0.0` / `0.5` |
| `flow3D_smooth` | Smooths flows; helps noisy data | `0` / `2` |
| `min_size` | Drop objects smaller than this (voxels) | `15` / `5` |
| `batch_size` | GPU memory vs speed. Lower it if you run out of memory | `8` / `8` |
| `preprocess` | `tophat` or `dog` halo suppression (candida) | – / `tophat` |
| `tophat_radius`, `dog_low`, `dog_high` | Preprocessing scale | – / `10`, `1.0`, `6.0` |

## Run it

```bash
source config.sh
SB="--partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES --cpus-per-task=$CANMAC_CPUS --mem=$CANMAC_MEM"

sbatch $SB --export=ALL,DATASET=ROI2,CHANNEL=macrophage scripts/slurm/20_segment_3d.sh
sbatch $SB --export=ALL,DATASET=ROI2,CHANNEL=candida    scripts/slurm/20_segment_3d.sh
```

When both arrays have finished:

```bash
pixi run python -m canmac.stages.segment --dataset ROI2 --finalize
```

??? tip "Controlling the array"
    | Want | Add |
    |---|---|
    | At most 8 GPUs at once (per-user limits) | `--array=0-119%8` |
    | Only a window of timepoints | `--array=40-79` |
    | One timepoint, interactively | `pixi run python -m canmac.stages.segment --dataset ROI2 --channel macrophage --t 40` |

    Re-submitting is always safe: finished timepoints are skipped.

## What it writes

| Path | Contents |
|---|---|
| `results/{DATASET}/{channel}_labels.zarr/t{NNN}` | 3D `uint16` instance labels for timepoint `NNN` |
| `results/{DATASET}/census.csv` | `dataset, channel, t, n_objects, fg_voxels` |

## Check it

- [ ] `census.csv` object counts change smoothly over time.
- [ ] In the viewer, each macrophage is **one** label: neighbours not merged, single
      cells not split.

    ```bash
    pixi run python viewers/view_labels.py ROI2 macrophage 100
    ```

- [ ] For *Candida*, masks follow the filaments without large halos. This is the known weak
      point; see [Known limitations](../help/limitations.md).

??? info "Experimental: micro-sam for *Candida*"
    `microsam_env/` holds a separate pixi environment and SLURM script for trying
    [micro-sam](https://github.com/computational-cell-analytics/micro-sam) on the pathogen
    channel. It is experimental and its script still contains paths from the original
    cluster. Edit `microsam_env/segment_candida_microsam.sh` and `microsam_env/pixi.toml`
    before you use it. Point viewers and engulfment at its output with
    `--store` / `--candida-store`.
