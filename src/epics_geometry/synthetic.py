"""Deterministic synthetic stub meshes for testing the scaler.

A stub has a flat base at z=0 (closed cap or open boundary) and a rounded tip.
Cross-sections are ellipses with optional taper and a small asymmetric term.
The body and base cap are separate textured meshes, so UV seams and material
seams both occur, as they do in generated GLBs.
"""

from __future__ import annotations

import math

import numpy as np
import trimesh
from PIL import Image


def _texture(color) -> Image.Image:
    img = np.zeros((16, 16, 3), dtype=np.uint8)
    img[:] = color
    img[::4, :] = 255  # stripes make UV mistakes visible
    return Image.fromarray(img)


def stub_meshes(
    length: float = 120.0,
    a: float = 32.0,
    b: float = 28.0,
    taper: float = 0.0,
    tip: float = 30.0,
    asym: float = 0.0,
    indent: float = 0.0,
    bend: float = 0.0,
    base: str = "closed",
    n_theta: int = 128,
    n_body: int = 90,
    n_tip: int = 30,
) -> list[trimesh.Trimesh]:
    """Return [body] or [body, base_cap] meshes in millimetres.

    ``a``/``b`` are base semi-axes along x/y, ``taper`` the fractional radius
    loss at the tip start, ``asym`` a one-sided bulge, ``indent`` a concave dent on the -x side, ``bend`` a lateral
    centreline offset at the tip as a fraction of length.
    """
    body_len = length - tip
    zb = np.linspace(0.0, body_len, n_body, endpoint=False)
    u = np.sin(0.5 * math.pi * np.arange(n_tip) / n_tip)  # denser near the apex
    z = np.concatenate([zb, body_len + tip * u])
    shrink = np.ones_like(z)
    in_tip = z > body_len
    shrink[in_tip] = np.sqrt(np.clip(1 - ((z[in_tip] - body_len) / tip) ** 2, 0, 1))
    taper_z = 1.0 - taper * np.minimum(z, body_len) / body_len
    theta = np.linspace(0, 2 * math.pi, n_theta + 1)  # last column duplicates first: UV seam
    rmod = 1.0 + asym * np.cos(theta - 0.7) - indent * np.exp(-(((theta - math.pi) / 0.35) ** 2))
    rx = (a * taper_z * shrink)[:, None] * rmod[None, :] * np.cos(theta)[None, :]
    ry = (b * taper_z * shrink)[:, None] * rmod[None, :] * np.sin(theta)[None, :]
    off = bend * length * (z / length) ** 2
    nz, nt = len(z), n_theta + 1
    ring = np.stack([rx + off[:, None], ry, np.repeat(z[:, None], nt, axis=1)], axis=-1).reshape(-1, 3)
    ring_uv = np.stack(
        [np.tile(theta / (2 * math.pi), nz), np.repeat(0.1 + 0.85 * z / length, nt)], axis=1
    )

    idx = np.arange(nz * nt).reshape(nz, nt)
    q00, q01 = idx[:-1, :-1], idx[:-1, 1:]
    q10, q11 = idx[1:, :-1], idx[1:, 1:]
    faces = np.concatenate(
        [np.stack([q00, q01, q11], -1).reshape(-1, 3), np.stack([q00, q11, q10], -1).reshape(-1, 3)]
    )
    apex_start = len(ring)
    apex = np.tile([bend * length, 0.0, length], (n_theta, 1))
    apex_uv = np.stack([(np.arange(n_theta) + 0.5) / n_theta, np.full(n_theta, 0.95)], axis=1)
    last = idx[-1]
    apex_faces = np.stack([last[:-1], last[1:], apex_start + np.arange(n_theta)], axis=1)
    vertices = np.concatenate([ring, apex])
    uv = np.concatenate([ring_uv, apex_uv])
    body = trimesh.Trimesh(
        vertices=vertices,
        faces=np.concatenate([faces, apex_faces]),
        visual=trimesh.visual.TextureVisuals(uv=uv, image=_texture((180, 120, 90))),
        process=False,
    )
    meshes = [body]
    if base == "closed":
        rim = ring[:n_theta]
        cap_v = np.concatenate([[[0.0, 0.0, 0.0]], rim])
        cap_uv = np.concatenate([[[0.5, 0.5]], 0.5 + 0.4 * rim[:, :2] / max(a, b)])
        k = np.arange(n_theta)
        cap_f = np.stack([np.zeros(n_theta, int), 1 + (k + 1) % n_theta, 1 + k], axis=1)
        meshes.append(
            trimesh.Trimesh(
                vertices=cap_v,
                faces=cap_f,
                visual=trimesh.visual.TextureVisuals(uv=cap_uv, image=_texture((60, 90, 160))),
                process=False,
            )
        )
    elif base != "open":
        raise ValueError("base must be 'closed' or 'open'")
    return meshes


def stub_scene(meshes: list[trimesh.Trimesh], transform: np.ndarray | None = None) -> trimesh.Scene:
    """Put meshes in a scene, optionally under a node transform."""
    scene = trimesh.Scene()
    names = ["body", "base_cap", "extra"]
    for i, m in enumerate(meshes):
        scene.add_geometry(m, geom_name=names[i] if i < 3 else f"part{i}", node_name=f"node_{i}", transform=transform)
    return scene
