# 4 · Cropping (optional)

<div class="mc-meta" markdown>
<span>**Runs on** CPU; preview needs a display</span>
<span>**Time** minutes</span>
<span>**Use for** Blender / Microscopy Nodes rendering</span>
</div>

## What it does

Cuts a sub-volume (an ROI box, optionally a range of timepoints) out of a large OME-Zarr at
**native resolution**. This is mainly for 3D rendering in Blender with
[Microscopy Nodes](https://github.com/oanegros/MicroscopyNodes), which handles at most about
4 GiB per volume grid. Cropping keeps full detail where downsampling would lose it.

Not needed for the analysis stages.

## Run it

Set the source store (default `full_stack/raw.zarr`):

```bash
export CANMAC_FULLSTACK_ZARR=data/full_stack/raw.zarr
```

**1. Preview a box in napari.** Nothing is written.

```bash
pixi run python scripts/cropping/11_preview_crop.py 0 --x0 1200 --x1 2700 --y0 300 --y1 1800
```

**2. Dry run.** Reports the output size and whether it fits under the Blender limit.

```bash
pixi run python scripts/cropping/12_crop_roi.py \
    --x0 1200 --x1 2700 --y0 300 --y1 1800 --out data/roi_a.zarr --dry-run
```

**3. Write it**, optionally for a range of timepoints:

```bash
pixi run python scripts/cropping/12_crop_roi.py \
    --x0 1200 --x1 2700 --y0 300 --y1 1800 --t0 0 --t1 40 --out data/roi_a.zarr
```

!!! tip "Coordinates are always full-resolution voxels"
    Whatever `--level` you preview at, the `--x0/--x1/...` numbers mean full-resolution
    (level-0) voxels. The numbers you choose in the preview mean the same thing to the
    cropper.

## Useful flags

| Flag | Meaning |
|---|---|
| `--channels` | Which channels to include (default `all`) |
| `--include-lysed` | Also include the discarded `FF00FF` channel |
| `--extra-levels N` | Number of downsampled levels to add to the output (default 2) |
| `--chunk-xy`, `--chunk-z` | Output chunk sizes (default 256, 64) |
| `--overwrite` | Replace an existing `--out` |
