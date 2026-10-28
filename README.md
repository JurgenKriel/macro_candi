# macro_candi

[![Docs](https://github.com/JurgenKriel/macro_candi/actions/workflows/docs.yml/badge.svg)](https://jurgenkriel.github.io/macro_candi/)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Cite](https://img.shields.io/badge/cite-CITATION.cff-green.svg)](CITATION.cff)

Quantitative analysis of live 3D lattice light-sheet timelapses of ***Candida*–macrophage**
host–pathogen interaction.

### 📖 **Documentation: <https://jurgenkriel.github.io/macro_candi/>**

The pipeline takes raw Zeiss CZI acquisitions through to two families of measurement:

1. **Engulfment / entrapment:** 3D containment of *Candida* objects inside macrophages.
2. **Hyphal network growth:** skeleton-graph metrics (length, tips, branch points) per
   object, tracked over time.

It also includes tools to convert, crop, and visually check every stage.

```
CZI ──▶ OME-Zarr ──▶ calibrate ──▶ 3D segmentation ──┬──▶ engulfment / containment
                     + bleach      (Cellpose-SAM, GPU) │
                                                       └──▶ hyphal MIP → skeleton → tracking
```

> **Status: research code.** Every measurement it writes is labelled **PROPOSED** and should
> be confirmed by eye (napari viewers and review notebooks are included for this) before it
> is treated as a result. See [Known limitations](https://jurgenkriel.github.io/macro_candi/help/limitations/).

## Quick start

Requires Linux + SLURM, [pixi](https://pixi.sh), and an NVIDIA GPU with **≥ 16 GB** VRAM.

```bash
git clone https://github.com/JurgenKriel/macro_candi.git && cd macro_candi
pixi install
pixi run pytest tests/ -q -m "not gpu"

source config.sh
sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES scripts/slurm/00_env_smoke.sh
```

Before running on a new cluster, read
**[Installation](https://jurgenkriel.github.io/macro_candi/getting-started/installation/)**.
You will need to pre-download the Cellpose weights for offline compute nodes and edit the
site-specific paths in `pixi.toml`. Then follow the
**[first-run checklist](https://jurgenkriel.github.io/macro_candi/getting-started/first-run/)**.

## Documentation map

| | |
|---|---|
| [Getting started](https://jurgenkriel.github.io/macro_candi/getting-started/) | Install, configure, first end-to-end run |
| [How it works](https://jurgenkriel.github.io/macro_candi/concepts/) | The method in plain terms, and the design principles |
| [Pipeline](https://jurgenkriel.github.io/macro_candi/pipeline/) | One page per stage: run it, what it writes, how to check it |
| [Review & QC](https://jurgenkriel.github.io/macro_candi/review/) | napari viewers and review notebooks |
| [Outputs](https://jurgenkriel.github.io/macro_candi/reference/outputs/) · [CLI](https://jurgenkriel.github.io/macro_candi/reference/cli/) | Every file, column and flag |
| [Troubleshooting](https://jurgenkriel.github.io/macro_candi/help/troubleshooting/) | Symptom → fix |

The docs are Markdown files in [`docs/`](docs/). Preview them locally with
`pip install -r docs/requirements.txt && mkdocs serve`.

## Repository layout

| Path | What it is |
|---|---|
| `canmac/io/` | Streaming reader, dataset manifest, channel resolution, calibration, photobleaching, atomic writes |
| `canmac/stages/` | Pipeline stages: `ingest`, `calibrate`, `segment`, `engulfment`, `hyphae2d`, `hyphae_track` |
| `scripts/` | Conversion, cropping and SLURM wrappers |
| `viewers/` · `notebooks/` | napari viewers and Jupyter review/tuning notebooks |
| `params/` | Cellpose-SAM parameter sidecars |
| `microsam_env/` | Isolated, experimental micro-sam environment |
| `tests/` | pytest suite (CPU + GPU-marked) |
| `docs/` · `mkdocs.yml` | Documentation website |

## License & citation

[GNU General Public License v3.0](LICENSE) © 2026 Jurgen Kriel. If you use this pipeline in
published work, please cite it (see [`CITATION.cff`](CITATION.cff), or GitHub's *"Cite this
repository"* button).
