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
  - id: ROI2                          # becomes results/ROI2/ and the --dataset value
    zarr_path: data/ROI2/raw.zarr     # relative to this file
    component: "0/0"                  # full-resolution array inside the store
    dims: {T: 120, C: 3, Z: 301, Y: 401, X: 369}
    voxel_um: 0.14499219272808386     # optional; checked against the store
    channels:
      candida: "FF0000"               # omero colour → channel role
      macrophage: "00FF00"
      discard: ["FF00FF"]             # never read
    bleach_correct:                   # optional; see Stage 3
      macrophage: true
      candida: false
```

List as many datasets as you like, one entry each. With a single dataset, `--dataset`
can be left off every command.

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

## Field reference

| Field | Required | Rule |
|---|---|---|
| `id` | yes | Letters, digits, `.`, `_`, `-`; unique. Used as a directory name. |
| `zarr_path` | yes | Store path, relative to the manifest file (or absolute). Must exist. |
| `dims` | yes | Positive integers `T, C, Z, Y, X`; must equal the on-disk array shape. |
| `channels.candida`, `channels.macrophage` | yes | Distinct 6-digit hex colours present in the store's `omero` metadata. |
| `channels.discard` | no | Colours that are never read. |
| `component` | no | Default `"0/0"`. |
| `voxel_um` | no | If given, must match the store's NGFF scale (checked by ingest). |
| `bleach_correct` | no | `{macrophage: bool, candida: bool}`; overrides the default policy. |
| `parallel_unit` | no | Default and only value: `timepoint`. |

Everything else (number of timepoints, voxel size, frame times) is read from the data.

## Using more than one manifest

Every stage and viewer reads, in order: `--manifest PATH` if given, else the
`CANMAC_MANIFEST` environment variable, else `manifest.yaml` at the repo root. Keeping one
manifest per experiment and switching with the variable is the tidy pattern:

```bash
export CANMAC_MANIFEST=/projects/exp-2027-01/manifest.yaml
python -m canmac.stages.ingest
python -m canmac.stages.engulfment --dataset well-B3 --all-t
```

!!! tip "Want to see a complete working manifest?"
    `python -m canmac.demo generate` writes one to `data/demo/manifest.yaml`. See
    [Try the demo](../getting-started/demo.md).
