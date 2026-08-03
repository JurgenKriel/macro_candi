#!/bin/bash
#SBATCH --job-name=seg3d
#SBATCH --output=logs/seg3d.%A_%a.log
#SBATCH --error=logs/seg3d.%A_%a.log
#SBATCH --time=08:00:00
#SBATCH --array=0-119
# 3D Cellpose-SAM segmentation — ONE SLURM array task per timepoint (the restart unit).
# Completed timepoints are skipped (per-timepoint .done sentinel), so re-submitting is
# idempotent. Channel is chosen by the CHANNEL env var.
#
#   sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES \
#          --cpus-per-task=$CANMAC_CPUS --mem=$CANMAC_MEM \
#          --export=ALL,DATASET=ROI2,CHANNEL=macrophage scripts/slurm/20_segment_3d.sh
#   ... then again with CHANNEL=candida
# Finalize the per-frame object census after the array drains:
#   pixi run python -m canmac.stages.segment --dataset ROI2 --finalize
set -euo pipefail
source "$(dirname "$0")/../../config.sh"
cd "$CANMAC_REPO"; mkdir -p logs
DATASET="${DATASET:-ROI2}"; CHANNEL="${CHANNEL:-macrophage}"
pixi run --frozen python -c "
import os, torch; n=torch.cuda.get_device_name(0); print('GPU:', n)
assert torch.cuda.is_available()
rej=os.environ.get('CANMAC_REJECT_GPU','');  assert not rej or rej not in n, f'refusing {n}'"
pixi run --frozen python -m canmac.stages.segment \
  --dataset "$DATASET" --channel "$CHANNEL" --t "$SLURM_ARRAY_TASK_ID"
