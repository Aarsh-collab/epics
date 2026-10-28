"""Generate a GLB from one photo with TRELLIS.2 on a CUDA GPU (e.g. a Sol node).

``trellis2``, ``o_voxel`` and ``torch`` are imported only when generation runs,
so the rest of ``epics_geometry`` installs and tests on a laptop without CUDA.
Their install is handled by ``sol/setup_env.sh``, not by this package.

Defaults reproduce the run logged in ``docs/stub_scaling.md`` (TRELLIS.2-4B,
seed 42, resolution 1024, decimation 500k, texture 2048, WebP), which used the
TRELLIS.2 Gradio app's settings: ``1024`` maps to the ``1024_cascade`` pipeline
and the GLB is remeshed with ``remesh_band=1``. Remeshing is a suspected cause
of the double-walled shells the stub scaler rejects, so it can be switched off.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

PIPELINE_TYPES = {"512": "512", "1024": "1024_cascade", "1536": "1536_cascade"}



@dataclass(frozen=True)
class TrellisSettings:
    model: str = "microsoft/TRELLIS.2-4B"
    seed: int = 42
    resolution: str = "1024"
    decimation_target: int = 500_000
    texture_size: int = 2048
    remesh: bool = True
    remesh_band: float = 1.0
    remesh_project: float = 0.0
    preprocess_image: bool = True


_pipeline_cache: dict[str, object] = {}


def _load_pipeline(model: str):
    if model not in _pipeline_cache:
        from trellis2.pipelines import Trellis2ImageTo3DPipeline

        pipeline = Trellis2ImageTo3DPipeline.from_pretrained(model)
        pipeline.cuda()
        _pipeline_cache[model] = pipeline
    return _pipeline_cache[model]


def _trellis_commit() -> str | None:
    """Commit of the TRELLIS.2 checkout that provided ``trellis2``, if it is a git repo."""
    try:
        import trellis2

        repo = Path(trellis2.__file__).resolve().parents[1]
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None


def runtime_info() -> dict:
    """GPU and scheduler facts worth keeping next to a generated mesh."""
    info = {
        "host": os.uname().nodename,
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "trellis2_commit": _trellis_commit(),
    }
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda"] = torch.version.cuda
        if torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
    except Exception:
        pass
    return info


def generate_glb(image_path: Path, output_path: Path, settings: TrellisSettings = TrellisSettings(),
                 model_input_path: Path | None = None) -> dict:
    """Run TRELLIS.2 on one image, write a GLB, and return a run record.

    An RGBA image with a transparent background (``photo_prep``) is used as is:
    TRELLIS.2's preprocessing skips its own background removal for it and only
    resizes, crops to the foreground and composites on black. That preprocessing
    is called here rather than inside ``pipeline.run`` so the exact model input
    can be saved to ``model_input_path``; the result is the same.
    """
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    if settings.resolution not in PIPELINE_TYPES:
        raise ValueError(f"resolution must be one of {sorted(PIPELINE_TYPES)}")
    import torch
    from PIL import Image
    import o_voxel

    if not torch.cuda.is_available():
        raise RuntimeError("TRELLIS.2 needs a CUDA GPU; run this inside a Sol GPU job or session")

    timing = {}
    t0 = time.perf_counter()
    pipeline = _load_pipeline(settings.model)
    timing["load_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    image = Image.open(image_path)
    has_alpha = image.mode == "RGBA"
    if settings.preprocess_image:
        image = pipeline.preprocess_image(image)
        if model_input_path is not None:
            image.save(model_input_path)
    mesh = pipeline.run(
        image,
        seed=settings.seed,
        preprocess_image=False,
        pipeline_type=PIPELINE_TYPES[settings.resolution],
    )[0]
    mesh.simplify(16777216)  # nvdiffrast limit
    timing["generate_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    glb = o_voxel.postprocess.to_glb(
        vertices=mesh.vertices,
        faces=mesh.faces,
        attr_volume=mesh.attrs,
        coords=mesh.coords,
        attr_layout=mesh.layout,
        voxel_size=mesh.voxel_size,
        aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        decimation_target=settings.decimation_target,
        texture_size=settings.texture_size,
        remesh=settings.remesh,
        remesh_band=settings.remesh_band,
        remesh_project=settings.remesh_project,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    glb.export(output_path, extension_webp=True)
    timing["extract_s"] = time.perf_counter() - t0
    del mesh
    torch.cuda.empty_cache()
    return {
        "image": str(image_path),
        "glb": str(output_path),
        "input_has_alpha": has_alpha,
        "model_input": str(model_input_path) if model_input_path and settings.preprocess_image else None,
        "settings": asdict(settings),
        "runtime": runtime_info(),
        "timing": timing,
    }


def add_settings_args(parser: argparse.ArgumentParser) -> None:
    d = TrellisSettings()
    parser.add_argument("--model", default=d.model)
    parser.add_argument("--seed", type=int, default=d.seed)
    parser.add_argument("--resolution", choices=sorted(PIPELINE_TYPES), default=d.resolution)
    parser.add_argument("--decimation-target", type=int, default=d.decimation_target)
    parser.add_argument("--texture-size", type=int, default=d.texture_size)
    parser.add_argument("--remesh", action=argparse.BooleanOptionalAction, default=d.remesh,
                        help="remesh during GLB export (suspected cause of double-walled shells)")
    parser.add_argument("--remesh-band", type=float, default=d.remesh_band)
    parser.add_argument("--remesh-project", type=float, default=d.remesh_project)
    parser.add_argument("--preprocess-image", action=argparse.BooleanOptionalAction, default=d.preprocess_image,
                        help="let TRELLIS.2 remove the background and crop")


def settings_from_args(args: argparse.Namespace) -> TrellisSettings:
    return TrellisSettings(
        model=args.model, seed=args.seed, resolution=args.resolution,
        decimation_target=args.decimation_target, texture_size=args.texture_size,
        remesh=args.remesh, remesh_band=args.remesh_band, remesh_project=args.remesh_project,
        preprocess_image=args.preprocess_image,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="epics-generate", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="GLB to write")
    add_settings_args(parser)
    args = parser.parse_args(argv)
    if args.output.exists():
        print(f"error: {args.output} exists", file=sys.stderr)
        return 2
    record = generate_glb(args.image, args.output, settings_from_args(args))
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
