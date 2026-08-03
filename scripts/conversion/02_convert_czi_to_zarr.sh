#!/bin/bash
#SBATCH --job-name=czi2zarr
#SBATCH --cpus-per-task=48
#SBATCH --mem=256G
#SBATCH --time=24:00:00
#SBATCH --output=logs/02_convert.%j.log
#SBATCH --error=logs/02_convert.%j.log
# STEP 02 — CZI -> OME-Zarr (bioformats2raw). This is THE required conversion: every
# analysis stage reads the resulting raw.zarr. bioformats2raw already emits a fused,
# multiscale pyramid, so no separate stitch/fusion step is needed for single-scene,
# non-mosaic acquisitions.
#
#   sbatch scripts/conversion/02_convert_czi_to_zarr.sh <input.czi> <out_dir>
# Produces <out_dir>/raw.zarr
set -euo pipefail
source "$(dirname "$0")/../../config.sh"
cd "$CANMAC_REPO"; mkdir -p logs
CZI="${1:?usage: 02_convert_czi_to_zarr.sh <input.czi> <out_dir>}"
OUT_DIR="${2:?missing <out_dir>}"
ZARR_OUT="${OUT_DIR}/raw.zarr"
[ -n "$CANMAC_CONVERT_ENV" ] && { source activate "$CANMAC_CONVERT_ENV" 2>/dev/null || conda activate "$CANMAC_CONVERT_ENV"; }
# G1GC handles these large heaps far better than the default collector.
export _JAVA_OPTIONS="$CANMAC_JAVA_OPTS"
mkdir -p "$OUT_DIR"
[ -e "$ZARR_OUT" ] && { echo "$ZARR_OUT exists — refusing to overwrite"; exit 1; }
bioformats2raw "$CZI" "$ZARR_OUT" \
  --max_workers="${SLURM_CPUS_PER_TASK:-16}" \
  --resolutions=3 \
  --tile_width=256 --tile_height=256
echo "wrote $ZARR_OUT"
