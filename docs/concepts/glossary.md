# Glossary

Anisotropy
:   The ratio of z-spacing to x/y-spacing. The reference data is isotropic (ratio 1.0), so
    Cellpose is run with `anisotropy=1.0`.

Array job (SLURM)
:   One `sbatch` submission that runs many near-identical tasks, each with its own
    `$SLURM_ARRAY_TASK_ID`. Here, one task = one timepoint.

Bleach correction
:   A per-frame multiplicative factor that undoes fluorescence fading, measured on the
    foreground (cells) only. Stored in `bleach.json` and applied when a volume is read.

Cellpose-SAM (`cpsam`)
:   The deep-learning model used for 3D instance segmentation (Cellpose 4, built on the
    Segment Anything backbone). It needs no object diameter.

CZI
:   Zeiss's proprietary microscopy file format.

`.done` sentinel
:   An empty marker file written **after** an output is complete. Stages use it to decide
    what to skip on a re-run.

Engulfed / partial / free
:   Per-frame classes for a *Candida* object, set by the fraction of its volume inside the
    best-overlapping macrophage. See [Stage 6](../pipeline/06-engulfment.md).

GRES
:   SLURM "generic resource", used here to request GPUs, e.g. `--gres=gpu:A30:1`.

Instance segmentation
:   Labelling each separate object with its own integer (1, 2, 3, …), as opposed to a
    plain foreground/background mask.

IoU (intersection over union)
:   Overlap between two masks divided by their combined area. Used to link hyphal objects
    between consecutive frames.

Label store
:   `results/{DATASET}/{channel}_labels.zarr`: one 3D `uint16` label array per timepoint,
    named `t000` … `t119`.

Manifest
:   `manifest.yaml`, the file that lists each dataset's path, dimensions, voxel size and
    channel colours. Every stage reads it.

MIP (maximum-intensity projection)
:   Collapsing a 3D stack to 2D by keeping the brightest value along z.

OME-Zarr
:   An open, chunked, multi-resolution format for bioimages. You can read any slice without
    loading the whole file.

pixi
:   The environment manager used here. It builds an exact, locked set of packages from
    `pixi.lock`.

PROPOSED
:   The label on every output: computed, but not yet confirmed by a person looking at it.

Skeleton
:   A one-pixel-wide centreline of a shape. Analysed as a graph with
    [skan](https://skeleton-analysis.org) to get lengths, tips and junctions.

Tip / junction
:   Skeleton nodes with one neighbour (a growing end) or more than two neighbours (a
    branch point).
