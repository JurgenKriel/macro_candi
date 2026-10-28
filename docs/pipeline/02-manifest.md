# 2 · Dataset manifest

<div class="mc-meta" markdown>
<span>**Runs on** you, in a text editor</span>
<span>**Time** minutes</span>
<span>**File** `manifest.yaml` (repo root, not committed)</span>
</div>

## What it does

`manifest.yaml` is the **single description of your data** that every later stage reads:
where each dataset is, its dimensions, voxel size, and which colour is which channel.

When it loads, the manifest is **checked against the files on disk**. If a declared
dimension disagrees with the actual array, it stops with an error, so a typo can't
quietly shift every measurement.

## Write it

```bash
cp manifest.example.yaml manifest.yaml
```

```yaml title="manifest.yaml"
datasets:
  - id: ROI2
    zarr_path: data/ROI2/raw.zarr     # relative to this file
    component: "0/0"                  # full-resolution array inside the store
    dims: {T: 120, C: 3, Z: 301, Y: 401, X: 369}
    voxel_um: 0.14499219272808386
    channels:
      candida: "FF0000"               # omero colour → channel role
      macrophage: "00FF00"
      discard: ["FF00FF"]             # never read
    parallel_unit: timepoint
  - id: ROI7
    zarr_path: data/ROI7/raw.zarr
    component: "0/0"
    dims: {T: 120, C: 3, Z: 301, Y: 291, X: 475}
    voxel_um: 0.14499219272808386
    channels:
      candida: "FF0000"
      macrophage: "00FF00"
      discard: ["FF00FF"]
    parallel_unit: timepoint
```

`manifest.yaml` is in `.gitignore` because it holds paths specific to your site.

## Find your channel colours

Look at the `omero` block in the store's metadata:

```bash
pixi run python -c "
import json; a = json.load(open('data/ROI2/raw.zarr/.zattrs'))
for i, c in enumerate(a['omero']['channels']): print(i, c['color'], c.get('label'))"
```

```text
0 FF00FF T1-LLS1_3
1 FF0000 ...
2 00FF00 ...
```

## Find your dimensions

```bash
pixi run python -c "import json; print(json.load(open('data/ROI2/raw.zarr/0/0/.zarray'))['shape'])"
```

The order is `[T, C, Z, Y, X]`.

## Check it

```bash
pixi run python -c "from canmac.io.manifest import load_manifest; import pprint; pprint.pprint(load_manifest())"
```

A valid manifest prints back with an extra `_store` field (the resolved absolute path) for
each dataset.

## Adapting to your own data

!!! warning "Current hard-coded assumptions"
    The code was written for the two reference timelapses, and some of their properties are
    **fixed in the code**, not read from the manifest:

    | Where | What is fixed |
    |---|---|
    | `canmac/io/manifest.py` | Exactly 2 datasets, with ids `ROI2` and `ROI7`; `T == 120`; colours `FF0000` / `00FF00` / `FF00FF` |
    | Every stage and viewer CLI | `--dataset` only accepts `ROI2` or `ROI7` |
    | `canmac/io/channels.py` | `COLORS` and `DISCARD` constants |
    | `canmac/io/bleach.py` | `APPLY_POLICY` per channel |
    | `hyphae2d`, `hyphae_track` | `--t-stop` defaults to `120` |

**Workarounds today:**

- **Same design, different movie:** give your datasets the ids `ROI2` and `ROI7` (two
  entries are required), with T=120 and the standard colours.
- **Different channel colours:** edit `COLORS` / `DISCARD` in `canmac/io/channels.py` and
  the `_EXPECTED_*` constants in `canmac/io/manifest.py` together.
- **Different number of timepoints or datasets:** edit `_EXPECTED_IDS` and `_EXPECTED_T` in
  `canmac/io/manifest.py`, the `choices=[...]` lists in the stage CLIs, and the SLURM
  `--array` range.

Making these read from the manifest is the top item on the
[roadmap](../help/limitations.md#roadmap).
