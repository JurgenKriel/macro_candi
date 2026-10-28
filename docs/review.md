# Review & QC

!!! quote ""
    Nothing in this pipeline should be trusted from numbers alone.

Every stage writes QC images to `results/{DATASET}/qc/`. Two more tools let you inspect
results interactively: **napari viewers** for 3D inspection, and **Jupyter notebooks** for
review across the whole timelapse.

## napari viewers

These need a machine with a display, such as a VNC or OpenOnDemand desktop session. Every
viewer also accepts `--check`, which prints its tables in the terminal without opening a
window. Use it on headless nodes or in scripts.

| Viewer | Use it to | Example |
|---|---|---|
| `viewers/view_labels.py` | Overlay 3D labels on raw data; tune Cellpose live | `pixi run python viewers/view_labels.py ROI2 macrophage 100` |
| `viewers/view_engulfment.py` | See *Candida* objects coloured by engulfment class | `pixi run python viewers/view_engulfment.py --t 41` |
| `viewers/view_candida_split.py` | Inspect yeast-vs-hyphae separation after halo suppression | `pixi run python viewers/view_candida_split.py 90 --preprocess dog` |

### Common recipes

```bash
# raw + labels for one timepoint
pixi run python viewers/view_labels.py ROI2 macrophage 100

# compare an alternative label store
pixi run python viewers/view_labels.py ROI2 candida 90 --store results/ROI2/other_labels.zarr

# re-segment one frame with new parameters, and save them if they look right
pixi run python viewers/view_labels.py ROI2 candida 90 --segment --cellprob 0.5 --save-params

# which timepoints have engulfment results?
pixi run python viewers/view_engulfment.py --list

# zoom on one Candida object and its macrophage
pixi run python viewers/view_engulfment.py --t 41 --candida 29 --margin 30
```

## Notebooks

| Notebook | Stage | What it shows |
|---|---|---|
| `notebooks/03_tune_cellpose.ipynb` | 5 | Edit parameters, re-run, see the overlay; saves `params/cpsam_{channel}.json` |
| `notebooks/04_hyphae2d_review.ipynb` | 7 | Per-timepoint panels next to their metrics, growth curves, labelled montage |
| `notebooks/05_engulfment_review.ipynb` | 6 | Metrics over time, whole-frame renders, zoomed per-event orthogonal views, threshold sensitivity |

Each notebook's first cell adds the repository root to `sys.path` **and** changes into it.
`canmac` is not pip-installed and the stages use paths relative to the repo root, so
**always run the first cell**.

### Notebooks under JupyterHub

JupyterHub does **not** apply pixi's activation environment, so Cellpose won't find its local
weights unless you register the kernel with them:

```bash
pixi run python -m ipykernel install --user --name canmac --display-name "canmac (pixi)"
```

Then edit `~/.local/share/jupyter/kernels/canmac/kernel.json` and add an `env` block:

```json title="kernel.json" hl_lines="6-8"
{
  "argv": ["…/.pixi/envs/default/bin/python", "-m", "ipykernel_launcher", "-f", "{connection_file}"],
  "display_name": "canmac (pixi)",
  "language": "python",
  "metadata": {"debugger": true},
  "env": {
    "CELLPOSE_LOCAL_MODELS_PATH": "/abs/path/to/models/cellpose"
  }
}
```

Choose the **canmac (pixi)** kernel in each notebook.

## A review routine that works

1. **Before the batch run:** tune on early, middle and late frames (notebook 03).
2. **After segmentation:** scan `census.csv` for jumps, then open the odd frames in
   `view_labels.py`.
3. **After engulfment:** open 5–10 events from each class in `view_engulfment.py`. Check the
   sensitivity table in notebook 05.
4. **After hyphae:** in notebook 04, check that skeletons follow filaments and that no
   track has a negative growth rate.
5. **Record what you checked** (which frames, which events) alongside the results.
