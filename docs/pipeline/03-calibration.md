# 3 · Calibration & photobleaching

<div class="mc-meta" markdown>
<span>**Runs on** CPU (login node or short job)</span>
<span>**Time** tens of minutes</span>
<span>**Reads** every timepoint once</span>
</div>

## What it does

For every dataset in the manifest:

1. **Calibration.** Reads the voxel size (and checks it is isotropic) and the **real
   timestamp of every frame** from the OME-XML `DeltaT` values. The frame interval is never
   assumed. The NGFF `t` scale is deliberately ignored because it does not carry real time.
2. **Photobleaching.** For each channel and timepoint, computes the mean intensity of the
   **foreground** (Otsu-thresholded cells, not background) and derives a correction factor
   `f[t] = S[reference] / S[t]`.
3. **QC plots** of raw vs corrected intensity over real time.

The raw data is never rewritten. Factors are applied **when a volume is read**, and only for
channels whose policy says so.

## Run it

```bash
pixi run python -m canmac.stages.ingest --manifest manifest.yaml
```

## What it writes

| File | Contents |
|---|---|
| `results/{DATASET}/calibration.json` | Voxel size in µm, isotropy check, per-timepoint timestamps (s) |
| `results/{DATASET}/bleach.json` | Per-(t, channel) factors, method, reference frame, and the `apply` flag with its reason |
| `results/{DATASET}/qc/bleach_decay_{channel}.png` | Raw vs corrected foreground mean over time |

## Should a channel be corrected?

This is a **scientific decision**, not a default. Open each QC plot and ask *why* the
foreground signal changes over time:

| The raw curve… | Likely cause | Policy |
|---|---|---|
| decays smoothly, roughly exponentially | photobleaching | `apply: true` |
| **rises** | more labelled material: growth, proliferation | `apply: false` |
| is flat | little bleaching | either; correction is ~1.0 |
| jumps at one frame | acquisition problem or focus change | investigate before deciding |

In the reference experiment, macrophages bleach (**corrected**) and *Candida* rises as it
grows (**not corrected**). Correcting the *Candida* channel would divide the growth signal
away.

To change the policy for a dataset, set it in the manifest and re-run this stage:

```yaml
    bleach_correct:
      macrophage: true
      candida: false
```

Without `bleach_correct`, the default above applies (`APPLY_POLICY` in
`canmac/io/bleach.py`). The decision and its reason are written into `bleach.json`, and
segmentation and the hyphal projections read each channel according to the same policy.

## Check it

- [ ] `calibration.json` shows the expected voxel size and a sensible time span
      (≈ 6 h for 120 frames at ~3 min).
- [ ] In each `bleach_decay_*.png`, the corrected curve is flat for corrected channels.
