#!/bin/bash
# Run the full pipeline on one capture folder on a Sol GPU node.
#   sbatch sol/run_pipeline.sbatch <capture_dir> <out_dir> [extra epics-pipeline args]
# e.g.
#   sbatch sol/run_pipeline.sbatch /scratch/$USER/captures/p01 /scratch/$USER/runs/p01
#   sbatch sol/run_pipeline.sbatch /scratch/$USER/captures/p01 /scratch/$USER/runs/p01-noremesh --no-remesh
# Submit from the repo root. Check the partition/QoS names with `sinfo` / `myfairshare` if rejected.
#SBATCH --job-name=epics-trellis
#SBATCH --partition=general
#SBATCH --qos=public
#SBATCH --gres=gpu:a100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --output=%x-%j.out
set -eo pipefail  # no -u: Lmod and conda activate reference unset variables

if [ $# -lt 2 ]; then
    echo "usage: sbatch sol/run_pipeline.sbatch <capture_dir> <out_dir> [epics-pipeline args]" >&2
    exit 2
fi
CAPTURE_DIR=$1 OUT_DIR=$2; shift 2
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
source sol/env.sh
nvidia-smi -L
epics-pipeline "$CAPTURE_DIR" --out "$OUT_DIR" "$@"
