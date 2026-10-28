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
| Dataset identity | The manifest validator and every CLI accept only `ROI2` / `ROI7`, with T=120 and fixed channel colours. |
| Site paths in `pixi.toml` | `[activation.env]` holds absolute paths from the original cluster and overrides `config.sh`. |
| Scripts with original paths | `microsam_env/` scripts and some docstrings refer to `/vast/scratch/users/kriel.j/...`. |
| Packaging | `canmac` is not pip-installable. Stages must run from the repo root. |
| Platform | `linux-64` only; needs SLURM for the batch scripts. |

## Roadmap

In rough priority order:

1. **Data-driven configuration:** read dataset ids, T, channel colours and bleach policy from
   the manifest, not code constants; remove the CLI `choices` restriction.
2. **Portable `pixi.toml`:** move site paths out of `[activation.env]`.
3. **Engulfment persistence:** require containment for ≥ N consecutive frames; classify fate.
4. **Better *Candida* masks:** filament-aware segmentation (e.g. micro-sam, ridge filters).
5. **Ground-truth set:** annotate a subset of frames and fit thresholds against it.
6. **Drift correction** before cross-time analysis.
7. **Packaging:** `pip install -e .` with console entry points (`canmac ingest`, `canmac segment`, …).
8. **CI:** run the CPU test suite and build the docs on every push.
