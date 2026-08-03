# Site configuration — EDIT THIS FILE (or export these vars) for your cluster.
# Every SLURM script in this repo sources it, so no path is hard-coded in the scripts.

# Absolute path to this repository (working directory for all jobs).
export CANMAC_REPO="${CANMAC_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# Where large outputs go (label stores, MIPs, CSVs, QC images). Needs plenty of space.
export CANMAC_RESULTS="${CANMAC_RESULTS:-$CANMAC_REPO/results}"

# pixi binary + package cache. Point the cache at fast, high-quota storage
# (a shared/network home dir with a small quota will fail mid-install).
export PATH="$HOME/.pixi/bin:$PATH"
export PIXI_CACHE_DIR="${PIXI_CACHE_DIR:-$HOME/.cache/pixi}"

# Pre-staged Cellpose-SAM weights. CELLPOSE_LOCAL_MODELS_PATH must be the PARENT
# directory containing the `cpsam` weight file (compute nodes are usually offline).
export CELLPOSE_LOCAL_MODELS_PATH="${CELLPOSE_LOCAL_MODELS_PATH:-$CANMAC_REPO/models/cellpose}"

# Pre-staged micro-sam weights (only needed for the micro-sam candida path).
export MICROSAM_CACHEDIR="${MICROSAM_CACHEDIR:-$CANMAC_REPO/models/microsam}"

# --- SLURM ---
export CANMAC_PARTITION_GPU="${CANMAC_PARTITION_GPU:-gpuq}"
# Typed GRES avoids landing on a GPU too small for the Cellpose-SAM transformer
# (a 12 GB card OOMs). Use e.g. "gpu:A30:1", "gpu:A100:1", or plain "gpu:1".
export CANMAC_GRES="${CANMAC_GRES:-gpu:1}"
# Substring of any GPU model to REFUSE (leave empty to disable the guard).
export CANMAC_REJECT_GPU="${CANMAC_REJECT_GPU:-P100}"
export CANMAC_CPUS="${CANMAC_CPUS:-8}"
export CANMAC_MEM="${CANMAC_MEM:-64G}"

# --- conversion (steps 01/02/10) ---
# Environment providing bioformats2raw / raw2ometiff / aicspylibczi.
export CANMAC_CONVERT_ENV="${CANMAC_CONVERT_ENV:-}"
export CANMAC_JAVA_OPTS="${CANMAC_JAVA_OPTS:--Xmx200G -XX:+UseG1GC -XX:G1HeapRegionSize=32m}"
