# Try the demo

Run the **whole pipeline in about two minutes on a laptop or login node**, with no microscope
data, no GPU and no cluster. The demo generates a small synthetic timelapse whose correct
answer is known, runs every stage on it, and scores the results.

```bash
pixi run demo
```

No pixi? Any Python ≥ 3.11 works:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-ci.txt
bash scripts/demo/run_demo.sh
```

The run ends with a scorecard:

```text
Synthetic demo check — results/DEMO

  PASS  voxel size (um)                          expected 0.5                    got 0.5
  PASS  timepoints                               expected 24                     got 24
  PASS  mean interval (min)                      expected 3.0                    got 3.0
  PASS  macrophage bleach factor, last frame     expected > 1.5                  got 1.99
  PASS  bleach policy (macrophage / candida)     expected apply / raw            got apply / raw
  PASS  macrophage count = 3                     expected 24/24 frames           got 24/24 frames
  PASS  candida count = 5                        expected 24/24 frames           got 24/24 frames
  PASS  engulfment class agreement (yeast)       expected >= 85%                 got 71/72 (99%)
  PASS  Y2 migrates free -> partial -> engulfed  expected all three seen         got ['engulfed', 'free', 'partial']
  PASS  H1 growth rate (um/min)                  expected 0.25 ± 25%             got 0.2519
  PASS  H2 growth rate (um/min)                  expected 0.3134 ± 25%           got 0.3203

ALL CHECKS PASSED
```

## What's in the synthetic movie

24 frames, 48 × 128 × 128 voxels at 0.5 µm, ~3 minutes apart (with small, realistic
timing jitter). It is stored exactly like a real converted acquisition: OME-Zarr v2,
big-endian `uint16`, omero channel colours, and per-plane timestamps in
`OME/METADATA.ome.xml`.

| Object | Channel | What happens | Tests |
|---|---|---|---|
| 3 macrophages | green `00FF00` | Fixed in place; signal fades to ~50 % (photobleaching) | segmentation, bleach correction |
| Yeast Y1 | red `FF0000` | Inside macrophage M1 throughout | `engulfed` |
| Yeast Y2 | red | Migrates into macrophage M2 | `free` → `partial` → `engulfed` |
| Yeast Y3 | red | Away from every macrophage | `free` |
| Hypha H1 | red | Grows straight at **0.25 µm/min** | growth rate |
| Hypha H2 | red | Grows and **branches at frame 8** | junctions, growth rate |
| Decoy | magenta `FF00FF` | Stored as the **first** channel, overlapping M1 | channels are found by colour, not position |

The two parts of the check worth reading closely:

- **Engulfment.** The yeast-frame that disagrees (Y2 at frame 11) sits right on the 90 %
  boundary. After blurring, the measured overlap lands just under it. That's the kind of
  borderline case [persistence across frames](../pipeline/06-engulfment.md#check-it) is
  meant to absorb.
- **Growth.** Rates are measured from 2D skeletons and real timestamps, and come within 3 %
  of the truth.

## What it runs

`scripts/demo/run_demo.sh` uses the **same commands** you will use on real data. Read it as
a worked example:

```bash
export CANMAC_MANIFEST=data/demo/manifest.yaml            # all stages read this manifest
python -m canmac.demo generate --out data/demo            # 1. make the data
python -m canmac.stages.ingest                            # 2. calibration + bleaching
python -m canmac.stages.segment --channel macrophage --t 0 \
       --params params/demo/classical_macrophage.json     # 3. one call per (channel, t)
python -m canmac.stages.segment --finalize
python -m canmac.stages.engulfment --all-t                # 4. engulfment
python -m canmac.stages.engulfment --finalize
python -m canmac.stages.hyphae2d --build-mip              # 5. hyphae
python -m canmac.stages.hyphae2d --all-t --method threshold --normalize none
python -m canmac.stages.hyphae2d --finalize
python -m canmac.stages.hyphae_track --min-frames 5       # 6. tracking + rates
python -m canmac.demo check                               # 7. score
```

`--dataset` is optional throughout because the demo manifest lists only one dataset.

!!! note "One difference from production: segmentation"
    The demo params select `"method": "classical"`, a CPU threshold-and-label backend, so the
    demo needs no GPU or model weights. It works on clean synthetic shapes. On real lattice
    light-sheet data it merges touching cells, which is why production uses Cellpose-SAM.
    With a GPU you can run Cellpose on the demo too: omit `--params`.

## Look at the results

Everything lands in `results/DEMO/`, in the same layout as a real run. Open the QC
images (`results/DEMO/qc/`), or use the viewers on a desktop session:

```bash
export CANMAC_MANIFEST=data/demo/manifest.yaml
pixi run python viewers/view_engulfment.py --t 12        # Y2 mid-way into M2
pixi run python viewers/view_labels.py DEMO candida 23
```

## What the demo does and doesn't tell you

!!! warning "Synthetic data checks the plumbing, not the biology"
    Passing means the code reads the files correctly, keeps units right, resolves
    channels, segments and tracks clean shapes, and classifies a known scene correctly. It
    says **nothing** about how well segmentation handles real *Candida* morphology. That
    still needs the [visual review](../review.md) on real data.

The same demo runs in CI on every push (see [Contributing](../about/contributing.md#tests)),
so a change that breaks any stage, or shifts a result, fails the build.

To start over: `rm -rf data/demo results/DEMO`.

---

**Next:** [Install on a cluster :material-arrow-right:](installation.md) for real data.
