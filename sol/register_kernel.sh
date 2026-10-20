#!/bin/bash
# Make the TRELLIS.2 env selectable as a Jupyter kernel in Open OnDemand.
#   bash sol/register_kernel.sh
# Then in OOD Jupyter pick the "EPICS TRELLIS.2" kernel and open notebooks/sol_pipeline.ipynb.
set -eo pipefail  # no -u: Lmod and conda activate reference unset variables
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_DIR/sol/env.sh"
pip install -q ipykernel
python -m ipykernel install --user --name epics-trellis2 --display-name "EPICS TRELLIS.2" \
    --env PYTHONPATH "$TRELLIS_DIR" \
    --env HF_HOME "$HF_HOME" \
    --env OPENCV_IO_ENABLE_OPENEXR 1 \
    --env PYTORCH_CUDA_ALLOC_CONF expandable_segments:True \
    --env ATTN_BACKEND "${ATTN_BACKEND:-flash_attn}" \
    --env EPICS_REPO "$REPO_DIR"
echo "Registered kernel 'EPICS TRELLIS.2'. Note: the kernel does not load the CUDA module;"
echo "that is only needed for compiling, not for running."
