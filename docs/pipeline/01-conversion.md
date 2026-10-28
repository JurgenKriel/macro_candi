# 1 · Conversion (CZI → OME-Zarr)

<div class="mc-meta" markdown>
<span>**Runs on** CPU · 48 cores · 256 GB</span>
<span>**Time** several hours per ~30 GB CZI</span>
<span>**Needs** `bioformats2raw`</span>
</div>

## What it does

Converts the Zeiss CZI file into an **OME-Zarr** store (`raw.zarr`): a chunked,
multi-resolution copy that later stages can read one timepoint at a time.

`bioformats2raw` writes a fused, multiscale pyramid directly, so for single-scene,
non-mosaic data **no separate stitching or fusion step is needed**.

## Run it

```bash
source config.sh
export CANMAC_CONVERT_ENV=/path/to/env-with-bioformats2raw

sbatch scripts/conversion/02_convert_czi_to_zarr.sh data/ROI2.czi data/ROI2
```

The script **refuses to overwrite** an existing `raw.zarr`. Delete or move the old one first
if you need to redo it.

### Optional extras

=== "Dump CZI metadata"

    Not needed downstream; useful to check dimensions and channels before converting.

    ```bash
    sbatch scripts/conversion/01_extract_metadata.sh data/ROI2.czi data/ROI2 /path/to/extract_metadata.py
    ```

=== "Export OME-TIFF (Imaris / Fiji)"

    ```bash
    sbatch scripts/conversion/10_zarr_to_ometiff.sh data/ROI2/raw.zarr data/ROI2/ROI2.ome.tiff
    ```

    This step can run longer than its time limit on large stacks. If it is cancelled,
    resubmit with a larger `--time`.

## What it writes

```text
data/ROI2/raw.zarr/
├── .zattrs            # includes omero channel colours
├── 0/0/               # full-resolution array (T, C, Z, Y, X), big-endian uint16
├── 0/1/, 0/2/         # downsampled levels
└── OME/METADATA.ome.xml   # per-plane timing (DeltaT) used in Stage 3
```

## Check it

- [ ] `data/ROI2/raw.zarr/0/0/.zarray` exists and its `shape` matches what you expect
      (`[120, 3, 301, 401, 369]` for the reference data).
- [ ] The log ends with `wrote data/ROI2/raw.zarr`.

!!! note "Expected warnings"
    Warnings about unknown `AcquisitionMode` or `Immersion` values are normal and harmless.
