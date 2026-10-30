# Known limitations

These affect how far the numbers can be trusted.

## Scientific

| Area | Status | What it means for you |
|---|---|---|
| Macrophage segmentation | :material-check-circle:{ style="color: #2e7d32" } solid | Usable after a visual check |
| *Candida* segmentation | :material-alert:{ style="color: #ef6c00" } weak link | The pathogen signal has a diffuse halo and uneven brightness along filaments. Cellpose-SAM tends to give inflated, blob-like masks, and a global threshold cuts filaments at dim stretches. `preprocess_volume` (top-hat / DoG) and `normalize_volume` (CLAHE / local-std) in `canmac/stages/segment.py` help, but need tuning per dataset and don't fully fix it. |
| Hyphal growth rates | :material-alert:{ style="color: #ef6c00" } not yet reliable | Because per-frame masks are unstable, tracks are short and some fitted rates come out negative. The tracking code works; the masks it receives are the problem. |
| Engulfment | :material-information:{ style="color: #1565c0" } per-frame only | No persistence test across frames yet. |
| Drift correction | :material-close-circle:{ style="color: #c62828" } none | Not needed for per-frame measurement. Apply it before any cross-time analysis that assumes a fixed frame. |
| Ground-truth validation | :material-close-circle:{ style="color: #c62828" } none | Thresholds are reasoned defaults, not fitted to a manually annotated set. |

## Software / portability

| Area | Status |
|---|---|
| Dataset identity | :material-check-circle:{ style="color: #2e7d32" } Any number of datasets, any ids, T, voxel size and channel colours, all read from the [manifest](../pipeline/02-manifest.md) and the data. |
| Testing without data | :material-check-circle:{ style="color: #2e7d32" } A [synthetic demo](../getting-started/demo.md) with known answers runs the whole pipeline in CI on every push. |
| Site paths in `pixi.toml` | :material-alert:{ style="color: #ef6c00" } `[activation.env]` holds absolute paths from the original cluster and overrides `config.sh`. |
| Scripts with original paths | :material-alert:{ style="color: #ef6c00" } `microsam_env/` scripts and some docstrings refer to `/vast/scratch/users/kriel.j/...`. |
| Reference-data tests | :material-information:{ style="color: #1565c0" } Tests tied to the original ROI2/ROI7 stores skip when that data is absent. |
| Packaging | :material-information:{ style="color: #1565c0" } `canmac` is not pip-installable. Stages must run from the repo root. |
| Platform | `linux-64` only; needs SLURM for the batch scripts. |

## Roadmap

In rough priority order:

1. ~~**Data-driven configuration**~~ :material-check: done: dataset ids, T, channel colours
   and bleach policy come from the manifest.
2. **Portable `pixi.toml`:** move site paths out of `[activation.env]`.
3. **Engulfment persistence:** require containment for ≥ N consecutive frames; classify fate.
4. **Better *Candida* masks:** filament-aware segmentation (e.g. micro-sam, ridge filters).
5. **Ground-truth set:** annotate a subset of real frames and fit thresholds against it.
6. **A small real example dataset** (e.g. a cropped ROI on Zenodo) alongside the synthetic demo.
7. **Drift correction** before cross-time analysis.
8. **Packaging:** `pip install -e .` with console entry points (`canmac ingest`, `canmac segment`, …).
9. ~~**CI**~~ :material-check: done: CPU tests, the demo and the docs build run on every push.
