#!/bin/bash
#SBATCH --job-name=hyphae2d
#SBATCH --time=02:00:00
#SBATCH --output=logs/hyphae2d.%j.log
#SBATCH --error=logs/hyphae2d.%j.log
# 2D hyphal-network pipeline: build the Z-MIP timelapse, segment every frame with
# Cellpose-SAM 2D, skeletonize, and measure the skan graph PER OBJECT.
# ONE job (not an array) so the model loads once and is reused across all frames.
#
#   sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES \
#          --export=ALL,DATASET=ROI2 scripts/slurm/30_hyphae2d.sh
set -euo pipefail
source "$(dirname "$0")/../../config.sh"
cd "$CANMAC_REPO"; mkdir -p logs
DATASET="${DATASET:-ROI2}"
pixi run --frozen python -m canmac.stages.hyphae2d --dataset "$DATASET" --build-mip
pixi run --frozen python -m canmac.stages.hyphae2d --dataset "$DATASET" --all-t --method cellsam
pixi run --frozen python -m canmac.stages.hyphae2d --dataset "$DATASET" --finalize
pixi run --frozen python -m canmac.stages.hyphae_track --dataset "$DATASET" --min-frames 5
