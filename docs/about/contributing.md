# Contributing

Bug reports, fixes and new stages are welcome.

## Reporting a problem

[Open an issue](https://github.com/JurgenKriel/macro_candi/issues/new) and include:

- the command you ran and the **full job log** (`logs/…`);
- the output of the smoke test (`scripts/slurm/00_env_smoke.sh`);
- your GPU model and cluster (if relevant);
- for result problems: the dataset, timepoint and object ids, and a screenshot from a viewer.

## Development setup

```bash
git clone https://github.com/JurgenKriel/macro_candi.git
cd macro_candi
pixi install
pixi run test      # CPU tests, any node
pixi run demo      # whole pipeline on synthetic data, scored vs ground truth (~2 min)
```

## Tests

| What | Where it runs | Needs |
|---|---|---|
| Unit tests + synthetic-data tests (`tests/test_demo.py`) | CI and locally | nothing |
| End-to-end demo (`scripts/demo/run_demo.sh`) | CI and locally | nothing |
| Reference-data tests | locally | the original ROI2/ROI7 stores; skip otherwise |
| GPU tests (`-m gpu`) | GPU node | CUDA + Cellpose weights |

The `tests` workflow installs the lightweight `requirements-ci.txt` (no torch, Cellpose or
napari), runs `pytest -m "not gpu"`, then runs the demo and **fails if any check against the
ground truth fails**. Its results and QC images are attached to each run as the
`demo-results` artifact. If you change a stage, run `pixi run demo` before pushing.

GPU tests are marked `@pytest.mark.gpu` and run on a GPU node:

```bash
sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES --wrap \
  "pixi run pytest tests/ -q -m gpu"
```

## Repository layout

| Path | What it is |
|---|---|
| `canmac/io/` | Streaming reader, manifest, channels, calibration, bleaching, atomic writes |
| `canmac/stages/` | `ingest`, `calibrate`, `segment`, `engulfment`, `hyphae2d`, `hyphae_track` |
| `scripts/conversion/` | CZI → OME-Zarr → OME-TIFF |
| `scripts/cropping/` | ROI preview and extraction |
| `scripts/slurm/` | SLURM wrappers per stage |
| `viewers/` | napari viewers |
| `notebooks/` | Tuning and review notebooks |
| `params/` | Cellpose-SAM parameter sidecars |
| `microsam_env/` | Separate, experimental micro-sam environment |
| `tests/` | pytest suite |
| `docs/`, `mkdocs.yml` | This website |

## Adding a stage

Follow the [design principles](../concepts/design.md#the-convention-for-every-new-stage):
stream with `get_view`, write shards via `canmac.io.atomic` with a `.done` marker last,
skip finished units, provide `--finalize`, write QC images, and label outputs **PROPOSED**.
Use `add_cli_args` / `resolve_cli` from `canmac.io.manifest` for `--dataset` / `--manifest`,
and take timepoint counts and voxel sizes from the manifest and store, never constants.
Add the stage to `scripts/demo/run_demo.sh`, and a check to `canmac.demo.check` if the
synthetic scene can test it.
Add a SLURM wrapper in `scripts/slurm/` that sources `config.sh`, and a page under
`docs/pipeline/`.

## Working on the docs

The site is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) from
the Markdown files in `docs/`.

```bash
pip install -r docs/requirements.txt
mkdocs serve          # live preview at http://127.0.0.1:8000
mkdocs build --strict # what CI runs; fails on broken links
```

Every page has an :material-file-edit-outline: edit button that opens the file on GitHub.
Pushes to `main` that touch `docs/` or `mkdocs.yml` redeploy the site automatically.
