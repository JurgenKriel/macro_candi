#!/bin/bash
#SBATCH --job-name=candida_microsam
#SBATCH --partition=gpuq
#SBATCH --gres=gpu:A30:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=01:00:00
#SBATCH --output=/vast/scratch/users/kriel.j/monash_lsm/logs/candida_microsam.%j.log
#SBATCH --error=/vast/scratch/users/kriel.j/monash_lsm/logs/candida_microsam.%j.log

# micro-sam (vit_l_lm) 3D candida segmentation on an A30 (24 GB). Isolated microsam_env.
# Usage: sbatch segment_candida_microsam.sh <TIMEPOINT>   (default 90)
set -euo pipefail
T="${1:-90}"
shift || true
EXTRA=("$@")   # pass-through knobs, e.g. --normalize clahe --foreground-threshold 0.6

MSENV="/vast/scratch/users/kriel.j/monash_lsm/microsam_env"
cd "$MSENV"
mkdir -p /vast/scratch/users/kriel.j/monash_lsm/logs
export PATH="$HOME/.pixi/bin:$PATH"
export PIXI_CACHE_DIR="/vast/scratch/users/kriel.j/pixi-cache"
# Real __cuda is present on the GPU node, so no CONDA_OVERRIDE_CUDA needed here.

echo "=== micro-sam candida t${T} on $(hostname) ==="
nvidia-smi --query-gpu=name,driver_version --format=csv || true
# reject the 12 GB P100 (heterogeneous gpuq) — vit_l wants headroom
pixi run --frozen python -c "import torch; d=torch.cuda.get_device_name(0); assert 'P100' not in d, d; print('GPU', d, 'cuda', torch.version.cuda)"
pixi run --frozen python candida_microsam.py --t "$T" --device cuda "${EXTRA[@]}"
echo "=== micro-sam candida t${T} done ==="
