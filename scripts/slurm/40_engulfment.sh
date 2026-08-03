#!/bin/bash
#SBATCH --job-name=engulf
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/engulf.%j.log
#SBATCH --error=logs/engulf.%j.log
# 3D containment (engulfment / entrapment) — CPU only. Reads the two label stores and
# measures, per timepoint, what fraction of each candida object lies inside each
# macrophage. Timepoints missing either channel are skipped.
#
#   sbatch --export=ALL,DATASET=ROI2 scripts/slurm/40_engulfment.sh
set -euo pipefail
source "$(dirname "$0")/../../config.sh"
cd "$CANMAC_REPO"; mkdir -p logs
DATASET="${DATASET:-ROI2}"
pixi run --frozen python -m canmac.stages.engulfment --dataset "$DATASET" --all-t
pixi run --frozen python -m canmac.stages.engulfment --dataset "$DATASET" --finalize
