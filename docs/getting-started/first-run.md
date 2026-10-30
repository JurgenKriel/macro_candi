# Your first run

This page takes one dataset (`ROI2` in the examples) from a raw CZI file to an engulfment
table and a hyphal growth table. If you haven't yet, run [the demo](demo.md) first: it
uses the same commands on synthetic data in two minutes. Tick the boxes as you go. Each step says **what to check**
before moving on, because a mistake caught early costs minutes and one caught late costs a
day of GPU time.

```bash
cd macro_candi
source config.sh
SB="--partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES --cpus-per-task=$CANMAC_CPUS --mem=$CANMAC_MEM"
```

---

### Step 1: Convert CZI to OME-Zarr

- [ ] Submit the conversion:

    ```bash
    sbatch scripts/conversion/02_convert_czi_to_zarr.sh data/ROI2.czi data/ROI2
    ```

- [ ] **Check:** `data/ROI2/raw.zarr/0/0/.zarray` exists. This step can take **several hours**
      for a ~30 GB CZI.

[Details →](../pipeline/01-conversion.md)

### Step 2: Describe the dataset in `manifest.yaml`

- [ ] Copy the template and fill in your dimensions and channel colours:

    ```bash
    cp manifest.example.yaml manifest.yaml
    ```

- [ ] **Check:** it loads without errors:

    ```bash
    pixi run python -c "from canmac.io.manifest import load_manifest; print(load_manifest())"
    ```

[Details →](../pipeline/02-manifest.md)

### Step 3: Calibrate and measure photobleaching

- [ ] Run (on the login node or as a short CPU job):

    ```bash
    pixi run python -m canmac.stages.ingest --manifest manifest.yaml
    ```

- [ ] **Check:** open `results/ROI2/qc/bleach_decay_*.png`. Decide, **per channel**, whether
      the decline is photobleaching (correct it) or biology (leave it). This is a scientific
      decision. [How to decide →](../pipeline/03-calibration.md#should-a-channel-be-corrected)

### Step 4: Tune segmentation on a few frames

- [ ] Open `notebooks/03_tune_cellpose.ipynb`, try parameters on an early, a middle and a
      late timepoint, and save them to `params/cpsam_{channel}.json`.
- [ ] **Check:** in napari, one label per cell, with no merged neighbours and no fragments:

    ```bash
    pixi run python viewers/view_labels.py ROI2 macrophage 60 --segment
    ```

[Details →](../pipeline/05-segmentation.md)

### Step 5: Segment every timepoint

- [ ] Submit one GPU array per channel (throttled to 8 tasks running at once):

    ```bash
    sbatch $SB --array=0-119%8 --export=ALL,DATASET=ROI2,CHANNEL=macrophage scripts/slurm/20_segment_3d.sh
    sbatch $SB --array=0-119%8 --export=ALL,DATASET=ROI2,CHANNEL=candida    scripts/slurm/20_segment_3d.sh
    ```

- [ ] When both arrays have finished, write the object census:

    ```bash
    pixi run python -m canmac.stages.segment --dataset ROI2 --finalize
    ```

- [ ] **Check:** `results/ROI2/census.csv`. Object counts should change smoothly over time.
      A sudden jump or drop usually means a bad frame, so look at it in the viewer.

!!! tip "If something fails partway"
    Just resubmit the same command. Finished timepoints are skipped automatically.

### Step 6: Engulfment

- [ ] ```bash
      sbatch --export=ALL,DATASET=ROI2 scripts/slurm/40_engulfment.sh
      ```
- [ ] **Check:** review a handful of `engulfed` and `partial` events with
      `notebooks/05_engulfment_review.ipynb` or
      `viewers/view_engulfment.py --t <t> --candida <id>`.

[Details →](../pipeline/06-engulfment.md)

### Step 7: Hyphal growth

- [ ] ```bash
      sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES \
             --export=ALL,DATASET=ROI2 scripts/slurm/30_hyphae2d.sh
      ```
- [ ] **Check:** `notebooks/04_hyphae2d_review.ipynb`. Do the skeletons trace the hyphae? Are
      the tracks long enough to fit a growth rate?

[Details →](../pipeline/07-hyphae.md)

---

## Monitoring jobs

```bash
squeue -u $USER                       # what is queued / running
sacct -j <jobid> --format=JobID,State,Elapsed,MaxRSS   # how it went
tail -f logs/seg3d.<jobid>_<task>.log # live log of one array task
```

Every log lands in `logs/` inside the repository.

## What you have now

```text
results/ROI2/
├── calibration.json          # voxel size + real timestamps
├── bleach.json               # per-frame correction factors
├── macrophage_labels.zarr/   # 3D labels, one array per timepoint
├── candida_labels.zarr/
├── census.csv                # objects per frame
├── engulfment.csv            # containment per Candida object per frame
├── hyphae2d_objects.csv      # skeleton metrics per object per frame
├── hyphae2d_tracks.csv       # objects linked across frames
├── hyphae2d_growth_rates.csv # µm/min per track
└── qc/                       # images for every stage
```

Every column is described in the [Outputs reference](../reference/outputs.md).
