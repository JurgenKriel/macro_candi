# macro_candi

Quantitative analysis of live 3D lattice light-sheet timelapses of ***Candida*–macrophage**
host–pathogen interaction.

The pipeline takes raw Zeiss CZI acquisitions through to two families of measurement:

1. **Engulfment / entrapment** — 3D containment of *Candida* objects inside macrophages.
2. **Hyphal network growth** — skeleton-graph metrics (length, tips, branch points) per
   object, tracked over time.

Plus tooling to convert, crop, and visually confirm every stage.

```
CZI ──▶ OME-Zarr ──▶ [crop] ──▶ segmentation ──┬──▶ engulfment / containment
  01/02    (raw.zarr)   11/12      (Cellpose-SAM)│
                                                └──▶ hyphal MIP → skeleton → tracking
```

> **Status: research code.** Every measurement it emits is labelled **PROPOSED** and is
> meant to be confirmed visually (napari viewers and review notebooks are included for
> exactly that) before being treated as a result. See [Known limitations](#known-limitations).

---

## Table of contents

- [What's in here](#whats-in-here)
- [Requirements](#requirements)
- [Installation on a new cluster](#installation-on-a-new-cluster)
- [Configuration](#configuration)
- [Stage 1 — Conversion (CZI → OME-Zarr)](#stage-1--conversion-czi--ome-zarr)
- [Stage 2 — Dataset manifest](#stage-2--dataset-manifest)
- [Stage 3 — Calibration & photobleaching](#stage-3--calibration--photobleaching)
- [Stage 4 — Cropping (optional)](#stage-4--cropping-optional)
- [Stage 5 — 3D segmentation](#stage-5--3d-segmentation)
- [Stage 6 — Engulfment / entrapment](#stage-6--engulfment--entrapment)
- [Stage 7 — Hyphal growth (2D MIP)](#stage-7--hyphal-growth-2d-mip)
- [Visual confirmation](#visual-confirmation)
- [Outputs reference](#outputs-reference)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)

---

## What's in here

| Path | What it is |
|---|---|
| `canmac/io/` | Streaming reader, dataset manifest, channel resolution, calibration, photobleaching, atomic writes |
| `canmac/stages/` | Pipeline stages: `ingest`, `calibrate`, `segment`, `engulfment`, `hyphae2d`, `hyphae_track` |
| `scripts/conversion/` | CZI → OME-Zarr → OME-TIFF (`bioformats2raw` / `raw2ometiff`) |
| `scripts/cropping/` | Preview and extract a sub-volume ROI (for Blender / Microscopy Nodes) |
| `scripts/slurm/` | SLURM wrappers for each pipeline stage |
| `viewers/` | napari viewers to visually confirm segmentation and engulfment |
| `notebooks/` | Parameter tuning and review notebooks (Jupyter) |
| `microsam_env/` | Isolated micro-sam environment for the *Candida* segmentation experiments |
| `params/` | Cellpose-SAM parameter sidecars (tuned per channel) |
| `tests/` | pytest suite (CPU tests + GPU-marked tests) |

**Design notes worth knowing before you run anything**

- **Streaming, never whole-4D.** A dataset is tens of GB; every stage reads one
  `(timepoint, channel)` volume at a time via `canmac.io.reader.get_view`.
- **Channels are resolved by colour, not index.** `canmac/io/channels.py` reads the OME-Zarr
  `omero` metadata and matches hex colours, so channel order changes don't silently
  mis-assign data. Edit `COLORS` there for your fluorophores.
- **Atomic writes + `.done` sentinels.** Every output is written to a temp name, renamed,
  then a sentinel is written last. A killed SLURM job therefore never leaves a
  present-but-incomplete artifact, and re-submitting any stage is idempotent.
- **One timepoint = one restart unit.** Segmentation fans out as a SLURM array over
  timepoints and skips completed ones.

---

## Requirements

**Software**

- Linux + SLURM
- [`pixi`](https://pixi.sh) for environment management (installs its own Python 3.12)
- NVIDIA GPU with CUDA for segmentation. **≥16 GB VRAM recommended** — the Cellpose-SAM
  transformer backbone OOMs on a 12 GB card at 3D volume sizes.
- For conversion only: `bioformats2raw` and `raw2ometiff` (Java, separate from the pixi env)

**Input data**

Single-scene, non-mosaic CZI timelapses. The reference datasets are
`T=120, C=3, Z=301, Y≈400, X≈370`, isotropic 0.145 µm voxels, ~3 min/frame.
Two channels are used (a pathogen channel and a macrophage channel); a third is excluded.

---

## Installation on a new cluster

```bash
git clone git@github.com:JurgenKriel/macro_candi.git
cd macro_candi

# 1. install pixi (once, per user)
curl -fsSL https://pixi.sh/install.sh | bash
export PATH="$HOME/.pixi/bin:$PATH"

# 2. point the package cache at storage with space and no quota surprises
export PIXI_CACHE_DIR=/path/with/space/pixi-cache

# 3. build the environment from the committed lockfile (exact, reproducible)
pixi install

# 4. sanity check on the login node
pixi run python -c "import canmac, zarr, dask, cellpose, skan, napari; print('ok')"
pixi run pytest tests/ -q -m "not gpu"
```

### Pre-stage the model weights (important)

Compute nodes usually have **no outbound internet**. If Cellpose tries to download
weights at job start it will hang until the wall clock kills it. Download once on a
login node:

```bash
mkdir -p models/cellpose
pixi run python -c "
from cellpose import models; models.CellposeModel(gpu=False)"   # downloads cpsam
# copy the downloaded 'cpsam' file into models/cellpose/
cp ~/.cellpose/models/cpsam models/cellpose/
```

`CELLPOSE_LOCAL_MODELS_PATH` must point at the **directory containing** `cpsam`,
not at the file itself.

### Verify on a GPU node

```bash
source config.sh
sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES scripts/slurm/00_env_smoke.sh
```

This asserts CUDA is genuinely available (no silent CPU fallback), that the GPU isn't one
you've black-listed, that the weights resolve locally, and runs the CPU test suite.

---

## Configuration

**All site-specific paths live in `config.sh`** — the scripts contain none. Edit it (or
export the variables) before running anything:

| Variable | Meaning |
|---|---|
| `CANMAC_REPO` | Absolute path to this repo (auto-detected) |
| `CANMAC_RESULTS` | Where outputs go (needs space) |
| `PIXI_CACHE_DIR` | pixi package cache — put on fast, high-quota storage |
| `CELLPOSE_LOCAL_MODELS_PATH` | **Directory containing** the `cpsam` weight file |
| `MICROSAM_CACHEDIR` | micro-sam weights (only for the micro-sam path) |
| `CANMAC_PARTITION_GPU` | GPU partition name |
| `CANMAC_GRES` | GRES request, e.g. `gpu:1` or typed `gpu:A30:1` |
| `CANMAC_REJECT_GPU` | Substring of a GPU model to refuse (default `P100`); empty disables |
| `CANMAC_CONVERT_ENV` | Conda env providing `bioformats2raw` / `raw2ometiff` |
| `CANMAC_JAVA_OPTS` | JVM options for the Java converters |

> On a heterogeneous GPU partition, request a **typed** GRES (`gpu:A30:1`). A bare
> `gpu:1` can land you on the smallest card in the partition, which will OOM.

---

## Stage 1 — Conversion (CZI → OME-Zarr)

`bioformats2raw` emits a fused, multiscale OME-Zarr pyramid directly, so for
single-scene non-mosaic data there is **no separate stitching or fusion step**.

```bash
source config.sh
export CANMAC_CONVERT_ENV=/path/to/env-with-bioformats2raw

# optional: dump CZI metadata (dims, channels, voxel size) — not required downstream
sbatch scripts/conversion/01_extract_metadata.sh data/ROI2.czi data/ROI2 /path/to/extract_metadata.py

# REQUIRED: CZI -> OME-Zarr  (produces data/ROI2/raw.zarr)
sbatch scripts/conversion/02_convert_czi_to_zarr.sh data/ROI2.czi data/ROI2

# optional: OME-Zarr -> pyramidal OME-TIFF for Imaris / Fiji
sbatch scripts/conversion/10_zarr_to_ometiff.sh data/ROI2/raw.zarr data/ROI2/ROI2.ome.tiff
```

Expect **hours** for step 02 on a ~30 GB CZI (48 cores / 256 GB). Harmless warnings about
unknown `AcquisitionMode` / `Immersion` values are normal.

Step 10 is **wall-clock sensitive** on large stacks — raise `--time` if it is cancelled.

---

## Stage 2 — Dataset manifest

The manifest is the single entry point every later stage reads. Create `manifest.yaml`
at the repo root:

```yaml
datasets:
  - id: ROI2
    zarr_path: data/ROI2/raw.zarr
    component: "0/0"                 # level-0 array inside the store
    dims: {T: 120, C: 3, Z: 301, Y: 401, X: 369}
    voxel_um: 0.14499219272808386
    channels:
      candida: "FF0000"              # omero hex colour -> channel role
      macrophage: "00FF00"
      discard: ["FF00FF"]            # never read
    parallel_unit: timepoint
```

`canmac/io/manifest.py` **cross-checks the declared dims against the on-disk array** and
fails loudly on any mismatch. Channel roles are matched on the `omero` colours in
`raw.zarr/0/.zattrs` — inspect that file to find your own colours.

---

## Stage 3 — Calibration & photobleaching

```bash
pixi run python -m canmac.stages.ingest --manifest manifest.yaml
```

Emits per dataset:

- `calibration.json` — voxel size (isotropy asserted) and the **real per-timepoint
  timestamps** parsed from the OME XML `DeltaT` values. Frame interval is never assumed;
  the NGFF `t` scale is deliberately ignored because it does not carry real time.
- `bleach.json` — per-(timepoint, channel) photobleaching correction factors, computed on
  the **foreground** (Otsu-masked) mean, stored and applied **lazily on read**. The raw
  data is never rewritten.
- `qc/bleach_decay_*.png` — raw-vs-corrected decay plots.

**Per-channel correction policy.** A channel is only corrected if its `bleach.json` record
has `"apply": true`. In the reference experiment the macrophage channel photobleaches
(corrected), while the *Candida* channel's foreground **rises** — that is biological
growth, not bleaching, so correcting it would divide the growth signal away. It is stored
with `"apply": false` and returned raw. **Check the QC plots for your own data and set this
policy accordingly** — it is a scientific decision, not a default.

---

## Stage 4 — Cropping (optional)

For rendering a sub-volume in Blender / Microscopy Nodes (which tops out around 4 GiB per
volume grid), crop at native resolution rather than downsampling.

```bash
export CANMAC_FULLSTACK_ZARR=data/full_stack/raw.zarr

# preview a candidate box in napari — writes nothing
pixi run python scripts/cropping/11_preview_crop.py 0 --x0 1200 --x1 2700 --y0 300 --y1 1800

# dry run reports the output size
pixi run python scripts/cropping/12_crop_roi.py --x0 1200 --x1 2700 --y0 300 --y1 1800 \
    --out data/roi_a.zarr --dry-run

# write it (optionally a range of timepoints)
pixi run python scripts/cropping/12_crop_roi.py --x0 1200 --x1 2700 --y0 300 --y1 1800 \
    --t0 0 --t1 40 --out data/roi_a.zarr
```

Coordinates are **always level-0 (native) voxels**, whatever `--level` you preview at, so
the numbers you settle on in the preview mean the same thing to the cropper.

---

## Stage 5 — 3D segmentation

Cellpose-SAM, native 3D (`do_3D=True`, `z_axis=0`, `anisotropy=1.0` for isotropic voxels,
`diameter=None` — Cellpose 4 is size-invariant).

```bash
source config.sh
SB="--partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES --cpus-per-task=$CANMAC_CPUS --mem=$CANMAC_MEM"

sbatch $SB --export=ALL,DATASET=ROI2,CHANNEL=macrophage scripts/slurm/20_segment_3d.sh
sbatch $SB --export=ALL,DATASET=ROI2,CHANNEL=candida    scripts/slurm/20_segment_3d.sh

# after the arrays drain — per-frame object census
pixi run python -m canmac.stages.segment --dataset ROI2 --finalize
```

Throttle with `--array=0-119%8` to stay inside a per-user GPU limit. Restrict the window
with e.g. `--array=40-79`. Re-submitting is safe: completed timepoints are skipped.

Tune parameters first with `notebooks/03_tune_cellpose.ipynb` (edit a params dict, re-run,
inspect the overlay); it writes `params/cpsam_{channel}.json`, the **same** sidecar the
batch reads, so there is no config drift between tuning and production.

Outputs: `results/{DATASET}/{channel}_labels.zarr` (one `t{NNN}` uint16 array per timepoint)
and `census.csv`.

---

## Stage 6 — Engulfment / entrapment

```bash
sbatch --export=ALL,DATASET=ROI2 scripts/slurm/40_engulfment.sh
# or directly:
pixi run python -m canmac.stages.engulfment --dataset ROI2 --all-t
pixi run python -m canmac.stages.engulfment --dataset ROI2 --finalize
```

For each *Candida* object it measures the fraction of its voxels inside each macrophage and
classifies by the best-overlapping one:

| Class | Rule (tunable) |
|---|---|
| `engulfed` | overlap ≥ `--engulf-frac` (default 0.9) |
| `partial` | `--touch-frac` ≤ overlap < engulf (default 0.05) — entrapment / phagocytic cup |
| `free` | overlap < touch |

`--dilate N` grows macrophages before testing, to separate adhesion from internalization.

Output: `results/{DATASET}/engulfment.csv` (one row per timepoint × object).
Review with `notebooks/05_engulfment_review.ipynb` — metrics over time, whole-frame renders,
zoomed per-event orthogonal views, and a threshold-sensitivity table.

> **This is per-frame geometry only.** A real engulfment event also requires **persistence**
> across consecutive frames; a single-frame overlap can be transient contact or a projection
> artefact. Persistence/fate classification is not implemented yet.

---

## Stage 7 — Hyphal growth (2D MIP)

Hyphae are quantified as a **network** (skeleton graph), never as instances — instance
segmenters do not represent branched filaments well.

```bash
sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES \
       --export=ALL,DATASET=ROI2 scripts/slurm/30_hyphae2d.sh
```

Which runs, in order:

1. `--build-mip` — Z-max-project every timepoint once into `{channel}_mip.zarr`
2. `--all-t --method cellsam` — segment each 2D frame (model loaded once), skeletonize,
   and measure **per object**: length, branches, tips (degree-1 nodes), junctions
   (degree>2), longest/mean branch, area
3. `--finalize` — `hyphae2d_objects.csv`, `hyphae2d_frame.csv`, growth-curve plot
4. `hyphae_track` — links objects **across frames by IoU overlap** into tracks, giving
   per-object growth trajectories and elongation rates (µm/min on the real time axis)

Review with `notebooks/04_hyphae2d_review.ipynb` (per-timepoint panels beside their metrics,
growth curves, labelled montage).

IoU-overlap linking is used because hyphae are sessile and grow by extension, so a filament
overlaps its own previous mask heavily — centroid/particle linkers handle that case badly.
It is **not** appropriate for motile cells; use a dedicated tracker (e.g. ultrack/btrack)
for macrophage tracking.

---

## Visual confirmation

Nothing in this pipeline should be trusted from numbers alone. On a machine with a display
(e.g. a VNC desktop):

```bash
pixi run python viewers/view_labels.py ROI2 macrophage 100          # raw + 3D labels
pixi run python viewers/view_labels.py ROI2 candida 90 --store results/ROI2/other_labels.zarr
pixi run python viewers/view_engulfment.py --t 41                   # containment classes
pixi run python viewers/view_engulfment.py --t 41 --candida 29      # crop to one pair
pixi run python viewers/view_engulfment.py --list                   # ready timepoints
```

All viewers accept `--check` to print the tables headlessly without opening a GUI.

**Notebooks under JupyterHub.** Register the pixi environment as a kernel with an explicit
`env` block — JupyterHub does **not** inherit pixi's activation environment, so without it
Cellpose will not find the local weights:

```bash
pixi run python -m ipykernel install --user --name canmac --display-name "canmac (pixi)"
# then add to ~/.local/share/jupyter/kernels/canmac/kernel.json:
#   "env": {"CELLPOSE_LOCAL_MODELS_PATH": "/abs/path/to/models/cellpose"}
```

Every notebook starts with a bootstrap cell that adds the repo root to `sys.path` **and**
`chdir`s to it, because `canmac` is not pip-installed and the stages use relative paths.

---

## Outputs reference

All under `results/{DATASET}/`:

| File | Contents |
|---|---|
| `calibration.json` | Voxel size, isotropy assertion, per-timepoint timestamps |
| `bleach.json` | Per-(t, channel) correction factors + `apply` policy |
| `{channel}_labels.zarr` | 3D instance labels, one `t{NNN}` array per timepoint |
| `census.csv` | Per-timepoint object counts per channel |
| `engulfment.csv` | Per (timepoint, candida object): overlap fraction, best macrophage, class |
| `{channel}_mip.zarr` | Z-max-projection timelapse |
| `candida_mip_labels.zarr` | 2D per-frame labels (so metrics can be recomputed without a GPU) |
| `hyphae2d_objects.csv` | Per (timepoint, object) skeleton metrics |
| `hyphae2d_frame.csv` | Per-frame aggregate + per-object statistics |
| `hyphae2d_tracks.csv` | Objects linked across frames (track ids, merge/split events) |
| `hyphae2d_growth_rates.csv` | Per-object elongation rate (µm/min) |
| `qc/*.png` | QC images for every stage |

---

## Known limitations

Stated plainly, because they affect how far the numbers can be trusted:

- **Pathogen-channel segmentation is the weak link.** The *Candida* fluorescence has a
  diffuse PSF halo and non-uniform intensity along filaments. Cellpose-SAM tends to produce
  inflated, blob-like masks; a global threshold cuts filaments at their dim stretches.
  `canmac/stages/segment.py` provides `preprocess_volume` (top-hat / DoG halo suppression)
  and `normalize_volume` (CLAHE / local-std contrast flattening) to mitigate this — both
  need tuning per dataset, and neither is a complete fix. Macrophage segmentation is solid.
- **Hyphal growth rates are not yet reliable.** Because per-frame masks are unstable,
  tracks are short and some fitted elongation rates come out negative — hyphae do not
  shrink. Treat the tracking machinery as working and the underlying masks as the problem.
- **Engulfment is per-frame geometry**, with no persistence test (see Stage 6).
- **No drift correction.** Not required for per-timepoint measurement, but it should be
  applied before any cross-time analysis that assumes a fixed frame.
- **No ground-truth validation yet.** Thresholds are reasoned defaults, not values fitted
  against a manually annotated set.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Job hangs at start, no output | Cellpose trying to download weights on an offline node. Pre-stage them; `CELLPOSE_LOCAL_MODELS_PATH` must be the **parent directory** of `cpsam`. |
| `CUDA out of memory` | Lower `batch_size` in the params sidecar (for micro-sam embeddings use `batch_size=1`); request a larger GPU; crop the Z range. |
| Segmentation silently very slow | It fell back to CPU. The stages assert `torch.cuda.is_available()` — run `scripts/slurm/00_env_smoke.sh` to confirm. |
| Masks are garbage / empty | Byte order. The store is big-endian `>u2`; volumes must be cast to native `float32` before reaching torch (the stages do this — replicate it in custom code). |
| `ModuleNotFoundError: canmac` in a notebook | The kernel's CWD is not the repo root. Run the bootstrap cell (adds repo to `sys.path` and `chdir`s). |
| `FileNotFoundError: results/.../bleach.json` | Same cause — stages use paths relative to the repo root. |
| pixi install fails with a quota/space error | Point `PIXI_CACHE_DIR` at storage with room. |
| `unsupported-platform` from pixi (micro-sam env) | That env declares a CUDA system requirement; on a GPU-less login node prefix commands with `CONDA_OVERRIDE_CUDA=12.4`. |
| Array jobs stuck pending | Per-user GPU limit. Throttle with `--array=0-119%8`; they drain in order. |

---

## License

[MIT](LICENSE) © 2026 Jurgen Kriel
