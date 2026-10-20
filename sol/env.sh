# Source this on Sol (login node, GPU job, or OOD terminal) before running anything:
#   source sol/env.sh
# Override any variable by exporting it first, e.g. EPICS_CUDA_MODULE=cuda-12.6.1-gcc-12.1.0.

# Everything large lives on /scratch so the home quota is not exhausted.
export EPICS_SCRATCH="${EPICS_SCRATCH:-/scratch/$USER/epics}"
export EPICS_ENV="${EPICS_ENV:-$EPICS_SCRATCH/envs/trellis2}"
export TRELLIS_DIR="${TRELLIS_DIR:-$EPICS_SCRATCH/TRELLIS.2}"
export HF_HOME="${HF_HOME:-$EPICS_SCRATCH/hf}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$EPICS_SCRATCH/pip-cache}"
export TMPDIR="${TMPDIR:-$EPICS_SCRATCH/tmp}"
mkdir -p "$EPICS_SCRATCH" "$HF_HOME" "$PIP_CACHE_DIR" "$TMPDIR"

module load mamba/latest
# torch 2.6 wheels are built for CUDA 12.4; the CUDA module only supplies nvcc
# for compiling the extensions. Pick a 12.x module if none was given.
if [ -z "${EPICS_CUDA_MODULE:-}" ]; then
    EPICS_CUDA_MODULE=$(module -t avail cuda 2>&1 | grep -E '^cuda-12\.4' | sort -V | tail -1)
    if [ -z "$EPICS_CUDA_MODULE" ]; then
        EPICS_CUDA_MODULE=$(module -t avail cuda 2>&1 | grep -E '^cuda-12\.' | sort -V | tail -1)
    fi
fi
export EPICS_CUDA_MODULE
if [ -n "$EPICS_CUDA_MODULE" ]; then
    module load "$EPICS_CUDA_MODULE"
else
    echo "warning: no cuda-12.x module found; run 'module avail cuda' and set EPICS_CUDA_MODULE" >&2
fi

# A100/A30 = 8.0, A10/RTX = 8.6, H100 = 9.0.
export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-8.0;8.6;9.0}"
export OPENCV_IO_ENABLE_OPENEXR=1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

if [ -d "$EPICS_ENV" ]; then
    eval "$(conda shell.bash hook)"
    conda activate "$EPICS_ENV"
    # TRELLIS.2 is not pip-installable; put its checkout on the path.
    export PYTHONPATH="$TRELLIS_DIR${PYTHONPATH:+:$PYTHONPATH}"
    # Written by setup_env.sh when flash-attn could not be built.
    if [ -f "$EPICS_ENV/.attn_backend" ]; then
        export ATTN_BACKEND="$(cat "$EPICS_ENV/.attn_backend")"
    fi
fi
