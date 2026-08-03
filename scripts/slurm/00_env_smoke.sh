#!/bin/bash
#SBATCH --job-name=canmac_smoke
#SBATCH --time=00:15:00
#SBATCH --output=logs/smoke.%j.log
#SBATCH --error=logs/smoke.%j.log
# Verify the environment on a GPU node BEFORE running anything long:
# full-stack imports, CUDA actually available (no silent CPU fallback), and the
# Cellpose weights resolving locally (compute nodes are usually offline).
#   sbatch --partition=$CANMAC_PARTITION_GPU --gres=$CANMAC_GRES scripts/slurm/00_env_smoke.sh
set -euo pipefail
source "$(dirname "$0")/../../config.sh"
cd "$CANMAC_REPO"; mkdir -p logs
nvidia-smi --query-gpu=name,driver_version --format=csv || true
pixi run --frozen python -c "
import os, torch
assert torch.cuda.is_available(), 'CUDA not available — refusing silent CPU fallback'
dev = torch.cuda.get_device_name(0); print('GPU:', dev, '| torch', torch.__version__, 'cuda', torch.version.cuda)
rej = os.environ.get('CANMAC_REJECT_GPU','')
assert not rej or rej not in dev, f'refusing {dev} (set CANMAC_REJECT_GPU to change)'
w = os.environ.get('CELLPOSE_LOCAL_MODELS_PATH'); print('weights dir:', w)
assert w and os.path.exists(os.path.join(w,'cpsam')), 'cpsam weights not pre-staged'
import zarr, dask, skimage, skan, cellpose, napari, networkx; print('full stack imports OK')
"
pixi run --frozen pytest tests/ -q -m "not gpu"
