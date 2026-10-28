# Troubleshooting

Find your symptom, then apply the fix. If it isn't listed,
[open an issue](https://github.com/JurgenKriel/macro_candi/issues/new) with the job log
attached.

## Installation & environment

??? failure "`pixi install` fails with a quota or disk-space error"
    pixi's package cache is on a small filesystem. Point it somewhere with room and retry:
    ```bash
    export PIXI_CACHE_DIR=/scratch/$USER/pixi-cache
    pixi install
    ```

??? failure "`unsupported-platform` from pixi (micro-sam env)"
    That environment declares a CUDA system requirement. On a login node with no GPU,
    prefix the command: `CONDA_OVERRIDE_CUDA=12.4 pixi install`.

??? failure "Smoke test prints a `weights dir` from someone else's cluster"
    `pixi.toml`'s `[activation.env]` overrides `config.sh`. Edit the
    `CELLPOSE_LOCAL_MODELS_PATH` and `XDG_CACHE_HOME` lines in `pixi.toml`. See
    [Installation step 3](../getting-started/installation.md#3-check-pixitoml-for-site-specific-paths).

??? failure "napari: cannot create cache directory / permission denied"
    `XDG_CACHE_HOME` points at a directory you can't write to (often set in `pixi.toml`).
    Set it to a writable scratch path.

## Jobs

??? failure "Job hangs at start, empty log, killed at the time limit"
    Cellpose is trying to download weights on an offline compute node. Pre-stage them
    ([Installation step 5](../getting-started/installation.md#5-pre-download-the-model-weights)).
    `CELLPOSE_LOCAL_MODELS_PATH` must be the **directory containing** `cpsam`.

??? failure "`CUDA out of memory`"
    - Request a bigger card with a typed GRES: `CANMAC_GRES=gpu:A100:1`.
    - Lower `batch_size` in `params/cpsam_{channel}.json` (micro-sam: use `batch_size=1`).
    - Crop the Z range.

??? failure "`AssertionError: CUDA not available` or `refusing <GPU name>`"
    Working as intended. The job landed on a node without a usable GPU, or on one listed in
    `CANMAC_REJECT_GPU`. Check `--partition` / `--gres`. To allow that card, set
    `CANMAC_REJECT_GPU=""`.

??? failure "Array tasks stuck in `PENDING` for a long time"
    You've hit a per-user GPU limit. Throttle with `--array=0-119%8`; tasks run in order.
    `squeue -u $USER -o "%.18i %.9P %.8T %.20R"` shows the reason.

??? failure "Conversion (`10_zarr_to_ometiff`) cancelled at the time limit"
    Large stacks need longer. Resubmit with e.g. `sbatch --time=48:00:00 …`.

??? failure "`raw.zarr exists — refusing to overwrite`"
    The conversion script protects existing stores. Move or delete the old `raw.zarr` if you
    really want to redo it.

## Data & results

??? failure "`ValueError: expected exactly 2 datasets` / `expected dataset ids {'ROI2', 'ROI7'}`"
    The manifest validator currently requires both reference datasets. See
    [Adapting to your own data](../pipeline/02-manifest.md#adapting-to-your-own-data).

??? failure "`dims.Y=… disagrees with on-disk .zarray Y=…`"
    The manifest's `dims` don't match the array. Read the true shape from
    `raw.zarr/0/0/.zarray` and fix the manifest. This check exists to catch exactly this.

??? failure "Masks are garbage or empty"
    Byte order. The store is big-endian `>u2`; volumes must be converted with
    `np.ascontiguousarray(vol).astype(np.float32)` before reaching torch. The stages do this.
    Do the same in any custom code.

??? failure "Segmentation is extremely slow"
    It is running on the CPU. Run `scripts/slurm/00_env_smoke.sh` on the same partition to
    confirm CUDA is visible.

??? failure "Negative hyphal growth rates"
    Not biology. Masks are unstable from frame to frame. See
    [Known limitations](limitations.md) and review the frames in notebook 04.

## Notebooks

??? failure "`ModuleNotFoundError: No module named 'canmac'`"
    The kernel isn't running from the repo root. Run the notebook's first (bootstrap) cell.

??? failure "`FileNotFoundError: results/.../bleach.json`"
    Same cause: stages use paths relative to the repo root. Run the bootstrap cell.

??? failure "Cellpose downloads weights / can't find `cpsam` in JupyterHub"
    The kernel doesn't inherit pixi's environment. Add the `env` block to `kernel.json`
    ([how](../review.md#notebooks-under-jupyterhub)).
