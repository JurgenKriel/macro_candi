#!/bin/bash
#SBATCH --job-name=czi_meta
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=logs/01_meta.%j.log
#SBATCH --error=logs/01_meta.%j.log
# STEP 01 (OPTIONAL) — dump CZI XML metadata (dims, channels, voxel size). Reads
# metadata only; no pixel data. The analysis pipeline does NOT depend on this step
# (canmac reads calibration straight from the converted OME-Zarr), so skip it if you
# do not have the extractor script.
#
#   sbatch scripts/conversion/01_extract_metadata.sh <input.czi> <out_dir> [extract_py]
set -euo pipefail
source "$(dirname "$0")/../../config.sh"
cd "$CANMAC_REPO"; mkdir -p logs
CZI="${1:?usage: 01_extract_metadata.sh <input.czi> <out_dir> [extract_py]}"
OUT_DIR="${2:?missing <out_dir>}"
EXTRACT_PY="${3:-${CANMAC_EXTRACT_PY:-}}"
[ -n "$CANMAC_CONVERT_ENV" ] && { source activate "$CANMAC_CONVERT_ENV" 2>/dev/null || conda activate "$CANMAC_CONVERT_ENV"; }
mkdir -p "$OUT_DIR"
if [ -z "$EXTRACT_PY" ]; then
  echo "No extractor given (arg 3 or \$CANMAC_EXTRACT_PY). Skipping — step 01 is optional."; exit 0
fi
python "$EXTRACT_PY" --czi "$CZI" --out-dir "$OUT_DIR"
