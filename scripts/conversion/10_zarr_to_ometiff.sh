#!/bin/bash
#SBATCH --job-name=zarr2tiff
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --output=logs/10_ometiff.%j.log
#SBATCH --error=logs/10_ometiff.%j.log
# STEP 10 (OPTIONAL) — raw.zarr -> pyramidal OME-TIFF for Imaris / Fiji viewing.
# Not needed by the analysis pipeline. NOTE: this step is time-limit sensitive on
# large stacks; raise --time if it is cancelled at the wall clock.
#
#   sbatch scripts/conversion/10_zarr_to_ometiff.sh <raw.zarr> <out.ome.tiff>
set -euo pipefail
source "$(dirname "$0")/../../config.sh"
cd "$CANMAC_REPO"; mkdir -p logs
ZARR_IN="${1:?usage: 10_zarr_to_ometiff.sh <raw.zarr> <out.ome.tiff>}"
OMETIFF_OUT="${2:?missing <out.ome.tiff>}"
[ -n "$CANMAC_CONVERT_ENV" ] && { source activate "$CANMAC_CONVERT_ENV" 2>/dev/null || conda activate "$CANMAC_CONVERT_ENV"; }
export _JAVA_OPTIONS="$CANMAC_JAVA_OPTS"
[ -e "$OMETIFF_OUT" ] && { echo "$OMETIFF_OUT exists — refusing to overwrite"; exit 1; }
raw2ometiff "$ZARR_IN" "$OMETIFF_OUT" \
  --max_workers="${SLURM_CPUS_PER_TASK:-16}" --compression=LZW
echo "wrote $OMETIFF_OUT"
