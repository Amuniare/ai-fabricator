"""Turning exact CAD shapes into triangle meshes for checks, pictures and slicing."""

from __future__ import annotations

import numpy as np
import trimesh
from build123d import Shape

# Fine enough for printing (well under a 0.4 mm nozzle line), coarse enough to be quick.
LINEAR_TOLERANCE = 0.02
ANGULAR_TOLERANCE = 0.2


def to_mesh(shape: Shape, tolerance: float = LINEAR_TOLERANCE) -> trimesh.Trimesh:
    vertices, triangles = shape.tessellate(tolerance, ANGULAR_TOLERANCE)
    mesh = trimesh.Trimesh(
        vertices=np.array([(v.X, v.Y, v.Z) for v in vertices], dtype=float),
        faces=np.array(triangles, dtype=np.int64).reshape(-1, 3),
        process=True,
    )
    mesh.merge_vertices()
    trimesh.repair.fix_normals(mesh)
    return mesh
