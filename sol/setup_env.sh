#!/bin/bash
# One-time install of TRELLIS.2 + this repo into a conda env on /scratch.
#
# Run on a GPU node, from the repo root, because the CUDA extensions are
# compiled against the GPU that is present:
#   OOD: start a Jupyter/desktop session with 1 A100 or H100, open a terminal, then
#   bash sol/setup_env.sh
# or from a login node:
#   salloc -p general -q public -G a100:1 -c 8 --mem=64G -t 3:00:00
#   bash sol/setup_env.sh
#
# Same steps as TRELLIS.2/setup.sh (--new-env --basic --flash-attn --nvdiffrast
# --nvdiffrec --cumesh --o-voxel --flexgemm), changed for a shared cluster: no
# sudo/apt, builds under /scratch instead of /tmp/extensions, env by path, and
# a pinned TRELLIS.2 commit. Safe to re-run; finished steps are skipped.
set -eo pipefail  # no -u: Lmod and conda activate reference unset variables

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRELLIS_COMMIT="${TRELLIS_COMMIT:-75fbf0183001ed9876c8dbb35de6b68552ee08bd}"
source "$REPO_DIR/sol/env.sh"

if ! command -v nvidia-smi >/dev/null || ! nvidia-smi -L >/dev/null 2>&1; then
    echo "error: no GPU visible. Run this inside a GPU session (see the header of this file)." >&2
    exit 1
fi
if ! command -v nvcc >/dev/null; then
    echo "error: nvcc not found. Set EPICS_CUDA_MODULE to a cuda-12.x module from 'module avail cuda'." >&2
    exit 1
fi
nvidia-smi -L
nvcc --version | tail -1
BUILD="$EPICS_SCRATCH/build"
mkdir -p "$BUILD"
export MAX_JOBS="${MAX_JOBS:-${SLURM_CPUS_ON_NODE:-8}}"

if [ ! -d "$EPICS_ENV" ]; then
    conda create -y -p "$EPICS_ENV" python=3.10
fi
eval "$(conda shell.bash hook)"
conda activate "$EPICS_ENV"
export PYTHONPATH="$TRELLIS_DIR"

python -c "import torch" 2>/dev/null || \
    pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124

if [ ! -d "$TRELLIS_DIR/.git" ]; then
    git clone --recursive https://github.com/microsoft/TRELLIS.2.git "$TRELLIS_DIR"
fi
git -C "$TRELLIS_DIR" checkout -q "$TRELLIS_COMMIT"
git -C "$TRELLIS_DIR" submodule update --init --recursive -q

# --basic (pillow-simd needs libjpeg headers from apt; plain pillow works)
pip install imageio imageio-ffmpeg tqdm easydict opencv-python-headless ninja trimesh \
    transformers gradio==6.0.1 tensorboard pandas lpips zstandard kornia timm
pip install git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8

# --flash-attn. Sparse attention needs flash_attn or xformers; fall back if the build fails.
rm -f "$EPICS_ENV/.attn_backend"
if ! python -c "import flash_attn" 2>/dev/null; then
    if ! pip install flash-attn==2.7.3 --no-build-isolation; then
        echo "flash-attn failed to install; using xformers instead" >&2
        pip install xformers==0.0.29.post3 --index-url https://download.pytorch.org/whl/cu124
        echo xformers > "$EPICS_ENV/.attn_backend"
    fi
fi

build_ext() {  # name, import name, git args...
    local name=$1 module=$2; shift 2
    if python -c "import $module" 2>/dev/null; then return; fi
    [ -d "$BUILD/$name" ] || git clone "$@" "$BUILD/$name"
    pip install "$BUILD/$name" --no-build-isolation
}
build_ext nvdiffrast nvdiffrast -b v0.4.0 https://github.com/NVlabs/nvdiffrast.git
build_ext nvdiffrec nvdiffrec_render -b renderutils https://github.com/JeffreyXiang/nvdiffrec.git
build_ext CuMesh cumesh --recursive https://github.com/JeffreyXiang/CuMesh.git
build_ext FlexGEMM flex_gemm --recursive https://github.com/JeffreyXiang/FlexGEMM.git
python -c "import o_voxel" 2>/dev/null || pip install "$TRELLIS_DIR/o-voxel" --no-build-isolation

# This repo (scaler, validator, pipeline) into the same env.
pip install -e "$REPO_DIR"

python - <<'EOF'
import importlib, torch
print("torch", torch.__version__, "cuda", torch.version.cuda, "gpu", torch.cuda.get_device_name(0))
for m in ["nvdiffrast.torch", "nvdiffrec_render", "cumesh", "flex_gemm", "o_voxel", "trellis2.pipelines", "epics_geometry.pipeline"]:
    importlib.import_module(m)
    print("ok", m)
try:
    import flash_attn; print("ok flash_attn", flash_attn.__version__)
except ImportError:
    import xformers; print("ok xformers", xformers.__version__, "(ATTN_BACKEND=xformers via sol/env.sh)")
EOF
echo "Setup complete. Next: bash sol/register_kernel.sh (for OOD Jupyter) or sbatch sol/run_pipeline.sbatch"
