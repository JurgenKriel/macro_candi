#!/bin/bash
# End-to-end run of the whole pipeline on the synthetic demo dataset — CPU only, ~1-2 minutes.
# Uses exactly the commands you would type for real data; only the segmentation params differ
# (params/demo/classical_*.json select the CPU "classical" backend instead of Cellpose-SAM).
#
#   pixi run demo                 # or: bash scripts/demo/run_demo.sh
#
# Writes data/demo/ (input) and results/DEMO/ (output), then scores the output against the
# generator's ground truth. Re-running is safe: finished timepoints are skipped. Set
# PYTHON to use an interpreter other than `python` (e.g. PYTHON="pixi run python").
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python}"
DEMO_DIR="${DEMO_DIR:-data/demo}"
export CANMAC_MANIFEST="$DEMO_DIR/manifest.yaml"   # every stage reads this manifest
export MPLBACKEND=Agg
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore::FutureWarning}"   # quieter scikit-image deprecations

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "1/7 generate synthetic dataset -> $DEMO_DIR"
if [ -f "$CANMAC_MANIFEST" ]; then echo "exists — skipping (rm -r $DEMO_DIR to regenerate)"
else $PY -m canmac.demo generate --out "$DEMO_DIR"; fi

step "2/7 calibration + photobleaching"
$PY -m canmac.stages.ingest

step "3/7 segment every timepoint (classical CPU backend)"
T=$($PY -c "from canmac.io.manifest import n_timepoints; print(n_timepoints('DEMO'))")
for CH in macrophage candida; do
  for ((t = 0; t < T; t++)); do
    $PY -m canmac.stages.segment --channel "$CH" --t "$t" --params "params/demo/classical_$CH.json"
  done
done
$PY -m canmac.stages.segment --finalize

step "4/7 engulfment"
$PY -m canmac.stages.engulfment --all-t
$PY -m canmac.stages.engulfment --finalize

step "5/7 hyphae: MIP -> 2D segmentation -> skeleton metrics"
$PY -m canmac.stages.hyphae2d --build-mip
$PY -m canmac.stages.hyphae2d --all-t --method threshold --normalize none --min-size 10 --closing 1
$PY -m canmac.stages.hyphae2d --finalize

step "6/7 hyphae: tracking + growth rates"
$PY -m canmac.stages.hyphae_track --min-frames 5

step "7/7 score against ground truth"
$PY -m canmac.demo check --out "$DEMO_DIR"
