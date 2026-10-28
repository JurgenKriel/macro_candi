# Configuration

All site-specific settings live in **`config.sh`** at the repository root. Every SLURM
script sources it, and no script contains a hard-coded path of its own.

Each variable uses the form `${VAR:-default}`, so you can either edit the defaults in the
file or `export` the variable before you submit a job.

```bash
source config.sh        # do this in every new shell before running sbatch
```

## Variables

| Variable | Default | What to set it to |
|---|---|---|
| `CANMAC_REPO` | auto-detected | Absolute path to this repository. Leave it unset. |
| `CANMAC_RESULTS` | `$CANMAC_REPO/results` | Where outputs go. Needs lots of space. |
| `PIXI_CACHE_DIR` | `$HOME/.cache/pixi` | pixi package cache. Use fast storage with a large quota. |
| `CELLPOSE_LOCAL_MODELS_PATH` | `$CANMAC_REPO/models/cellpose` | **Directory containing** the `cpsam` weight file. |
| `MICROSAM_CACHEDIR` | `$CANMAC_REPO/models/microsam` | micro-sam weights. Only needed for the experimental micro-sam path. |
| `CANMAC_PARTITION_GPU` | `gpuq` | Name of your GPU partition (`sinfo -s` lists them). |
| `CANMAC_GRES` | `gpu:1` | GPU request. Prefer a **typed** request such as `gpu:A30:1`. |
| `CANMAC_REJECT_GPU` | `P100` | Substring of a GPU model to refuse. Empty disables the check. |
| `CANMAC_CPUS` | `8` | CPUs per segmentation task. |
| `CANMAC_MEM` | `64G` | Memory per segmentation task. |
| `CANMAC_CONVERT_ENV` | *(empty)* | Conda env providing `bioformats2raw` / `raw2ometiff`. |
| `CANMAC_JAVA_OPTS` | `-Xmx200G …` | JVM options for the Java converters. |

!!! warning "Ask for a typed GPU"
    On a partition with mixed cards, a bare `gpu:1` can land on the smallest card, which
    runs out of memory partway through a volume. Request the card type explicitly, e.g.
    `CANMAC_GRES=gpu:A30:1`. The `CANMAC_REJECT_GPU` check is a safety net, not a substitute.

!!! danger "`pixi.toml` overrides `config.sh`"
    If you did not edit the `[activation.env]` block in `pixi.toml` during
    [installation](installation.md#3-check-pixitoml-for-site-specific-paths), its
    `CELLPOSE_LOCAL_MODELS_PATH` replaces the value from `config.sh` inside every
    `pixi run`. The smoke test prints the directory it ended up using, so check that line.

## Keeping local settings out of git

`config.local.sh` is already listed in `.gitignore`. A tidy pattern is to keep your
cluster's values there and source both files:

```bash title="config.local.sh"
export CANMAC_RESULTS=/scratch/$USER/canmac-results
export CANMAC_PARTITION_GPU=gpu
export CANMAC_GRES=gpu:A100:1
export CELLPOSE_LOCAL_MODELS_PATH=/scratch/$USER/models/cellpose
```

```bash
source config.local.sh && source config.sh
```

`config.sh` keeps any value that is already set, so your local overrides win.

## Segmentation parameters

Cellpose settings are stored per channel in `params/cpsam_{channel}.json`. The tuning notebook
writes these files and the batch jobs read them, so tuning and production always use the
same values. See [Stage 5 · Segmentation](../pipeline/05-segmentation.md#tune-before-you-run).

## Channel colours

Channels are identified by the hex colour recorded in the OME-Zarr metadata, not by their
position. The defaults are:

| Role | Colour |
|---|---|
| *Candida* | `FF0000` (red) |
| Macrophage | `00FF00` (green) |
| Discarded (lysed-cell marker) | `FF00FF` (magenta) |

If your acquisition uses different colours, see
[Stage 2 · Manifest](../pipeline/02-manifest.md#find-your-channel-colours).

---

**Next:** [Your first run :material-arrow-right:](first-run.md)
