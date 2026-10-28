# Installation

The environment is managed by [pixi](https://pixi.sh). It installs its own Python 3.12 and
rebuilds the exact package versions recorded in the committed `pixi.lock`, so two clusters
end up with identical software.

## 1. Clone and install pixi

Run these on a **login node** (it needs internet access).

```bash
git clone https://github.com/JurgenKriel/macro_candi.git
cd macro_candi

# install pixi once per user
curl -fsSL https://pixi.sh/install.sh | bash
export PATH="$HOME/.pixi/bin:$PATH"
```

## 2. Point the package cache at roomy storage

pixi downloads several GB of packages (PyTorch with CUDA is most of it). A home directory
with a small quota will fail partway through the install.

```bash
export PIXI_CACHE_DIR=/path/with/space/pixi-cache
```

## 3. Check `pixi.toml` for site-specific paths

!!! danger "Do this before `pixi install`"
    The `[activation.env]` block in `pixi.toml` currently sets **absolute paths from the
    original cluster**:

    ```toml
    [activation.env]
    CELLPOSE_LOCAL_MODELS_PATH = "/vast/scratch/users/kriel.j/cellpose_models"
    XDG_CACHE_HOME = "/vast/scratch/users/kriel.j/.cache"
    ```

    pixi applies these on every `pixi run`, so they **override** whatever you export or set
    in `config.sh`. On any other machine, change both values to directories you own (for
    example `$PWD/models/cellpose` and a scratch cache directory) or delete the lines and
    rely on `config.sh`.

## 4. Build the environment

```bash
pixi install
```

Then run a quick check on the login node:

```bash
pixi run python -c "import canmac, zarr, dask, cellpose, skan, napari; print('ok')"
pixi run pytest tests/ -q -m "not gpu"
```

Tests that need the reference datasets **skip** themselves when the data is not present, so
a fresh clone still gives a clean run.

## 5. Pre-download the model weights

Compute nodes usually have **no internet**. If Cellpose tries to download its weights when a
job starts, the job hangs until SLURM kills it at the time limit, with nothing in the log.
Download the weights once on the login node:

```bash
mkdir -p models/cellpose
pixi run python -c "from cellpose import models; models.CellposeModel(gpu=False)"
cp ~/.cellpose/models/cpsam models/cellpose/
```

!!! note "Directory, not file"
    `CELLPOSE_LOCAL_MODELS_PATH` must be the **directory that contains** `cpsam`
    (e.g. `models/cellpose`), not the path of the `cpsam` file itself.

## 6. Verify on a GPU node

```bash
source config.sh
sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES scripts/slurm/00_env_smoke.sh
```

Check `logs/smoke.<jobid>.log` when it finishes. A healthy run prints:

```text
GPU: NVIDIA A30 | torch 2.x cuda 12.4
weights dir: /your/path/models/cellpose
full stack imports OK
```

followed by passing tests. The script deliberately **fails** if:

- CUDA is not available (rather than running ~100× slower on the CPU),
- the GPU matches `CANMAC_REJECT_GPU` (default: `P100`), or
- the `cpsam` weights are not where `CELLPOSE_LOCAL_MODELS_PATH` says.

## Optional: conversion tools

[Stage 1 · Conversion](../pipeline/01-conversion.md) uses `bioformats2raw` and `raw2ometiff`,
which are Java tools outside the pixi environment. Install them into a separate conda
environment and set `CANMAC_CONVERT_ENV` to its path. For example:

```bash
mamba create -p /path/to/convert-env -c ome -c conda-forge bioformats2raw raw2ometiff
```

## Optional: Jupyter kernel

To use the review notebooks under JupyterHub / OpenOnDemand, see
[Review & QC → Notebooks](../review.md#notebooks-under-jupyterhub).

---

**Next:** [Configuration :material-arrow-right:](configuration.md)
