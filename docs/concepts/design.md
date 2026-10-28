# Design principles

These decisions shape how every stage behaves. Knowing them makes the pipeline predictable,
and tells you what to keep if you write your own stage.

## Stream; never load the whole 4D array

A dataset is tens of GB. Every stage reads **one `(timepoint, channel)` volume at a time**
through `canmac.io.reader.get_view`. Memory use is set by the size of one volume, not the
length of the movie.

```python
from canmac.io.reader import get_view

vol = get_view("ROI2", "macrophage", t=40).compute()   # one (Z, Y, X) volume
```

## Identify channels by colour, not position

Converters don't always keep channels in the same order. Reading "channel 1" when you meant
"channel 2" fails silently: everything runs, and you analyse the wrong cells.

`canmac/io/channels.py` looks up each channel by the hex colour stored in the OME-Zarr
`omero` metadata. It raises an error if a colour is missing or appears twice.

## Fail loudly

The code checks its assumptions and stops with a clear message, instead of continuing with
bad values:

- the manifest's declared dimensions are compared with the array on disk;
- voxel isotropy is asserted;
- segmentation refuses to run without CUDA (a CPU fallback would be ~100× slower and use up
  the job's time limit);
- segmentation refuses GPUs listed in `CANMAC_REJECT_GPU`.

## Atomic writes and `.done` markers

Every output is written to a temporary name, renamed into place, and **only then** gets a
`.done` sentinel file next to it.

```text
t042.json.tmp  →  t042.json  →  t042.json.done
   (writing)       (renamed)     (marked complete)
```

As a result:

- a job killed partway never leaves a file that *looks* finished;
- a stage skips any unit that already has its `.done` marker;
- **re-submitting any stage is always safe.** It picks up where it stopped.

## One timepoint = one restart unit

Segmentation runs as a SLURM **job array** with one task per timepoint. If 3 of 120 tasks
fail, resubmitting the array re-runs only those 3.

## Raw data is read-only

Nothing ever writes into `raw.zarr`. Corrections (bleaching) are stored as small sidecar
files and applied when a volume is read. All outputs go under `results/{DATASET}/`.

## One parameter file for tuning and production

The tuning notebook and the batch job read the same `params/cpsam_{channel}.json` file. The
values you checked by eye are the values the batch job uses.

## The convention for every new stage

If you add a stage, follow the existing ones:

1. read through `get_view` (streaming, channels by colour, bleach policy applied);
2. write per-unit shards with `canmac.io.atomic` and a `.done` marker last;
3. skip units that are already done;
4. provide a `--finalize` step that combines shards into one CSV;
5. write QC images and label the outputs **PROPOSED**.

See [Contributing](../about/contributing.md).
