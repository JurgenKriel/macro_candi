# Getting started

Setting up macro_candi on a new cluster takes three steps. Do them in order: each one
checks the one before it.

!!! tip "Want to see it work first?"
    [Try the demo](demo.md): the whole pipeline on a synthetic dataset, CPU only, about
    2 minutes, with a pass/fail scorecard against the known answer.

<div class="grid cards" markdown>

-   :material-numeric-1-circle:{ .lg } **[Install](installation.md)**

    ---

    Clone the repo, build the pinned environment with `pixi`, pre-download the model
    weights, and confirm a GPU node can see CUDA.

    *About 30 minutes, mostly waiting on downloads.*

-   :material-numeric-2-circle:{ .lg } **[Configure](configuration.md)**

    ---

    Tell the pipeline where your results, model weights and GPU partition are, all in
    one file: `config.sh`.

    *About 5 minutes.*

-   :material-numeric-3-circle:{ .lg } **[First run](first-run.md)**

    ---

    A checklist that takes one dataset from CZI to an engulfment table, with what to look
    at after each step.

    *A few hours of cluster time, a few minutes of your attention per step.*

</div>

## Before you begin

Have these ready:

| You need | Why |
|---|---|
| A Linux account on a SLURM cluster | Every heavy stage is a SLURM job |
| Access to a GPU partition with **≥ 16 GB VRAM** cards (A30, A100, …) | Cellpose-SAM runs out of memory on 12 GB cards (e.g. P100) at 3D volume sizes |
| A storage location with **tens of GB free** per dataset | A raw dataset is ~30 GB; label stores and projections add more |
| A login node **with internet access** | To build the environment and download weights once. Compute nodes can be offline. |
| A desktop session (VNC / OpenOnDemand) | For the napari viewers. Optional, but you need it to confirm results visually. |
| `bioformats2raw` (and optionally `raw2ometiff`) | Only for [Stage 1 · Conversion](../pipeline/01-conversion.md). Java tools, installed separately. |

!!! tip "New to SLURM or pixi?"
    You only need a few commands. `sbatch script.sh` submits a job, `squeue -u $USER` lists
    your jobs, and `pixi run <cmd>` runs a command inside the project's environment. The
    pages show every command you need to type.
