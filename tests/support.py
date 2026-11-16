import importlib.util
import math
from pathlib import Path
import sys

import rhino3dm


ROOT = Path(__file__).resolve().parents[1]


def load_script(relative_path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def punched_wall():
    """RMF-01: one closed 10 x 10 ft Brep with a 4 x 4 ft through-window."""
    def ring(points):
        return rhino3dm.PolylineCurve([rhino3dm.Point3d(x, y, 0) for x, y in points])

    extrusion = rhino3dm.Extrusion.Create(
        ring([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]), 0.5, True
    )
    assert extrusion.AddInnerProfile(ring([(3, 3), (3, 7), (7, 7), (7, 3), (3, 3)]))
    brep = extrusion.ToBrep(True)
    brep.Transform(rhino3dm.Transform.Rotation(
        math.pi / 2, rhino3dm.Vector3d(1, 0, 0), rhino3dm.Point3d(0, 0, 0)
    ))
    assert brep.IsValid and brep.IsSolid
    assert (len(brep.Faces), len(brep.Edges)) == (10, 24)
    return brep


def panel(x0, z0, x1, z1):
    return rhino3dm.Brep.CreateFromBoundingBox(rhino3dm.BoundingBox(
        rhino3dm.Point3d(x0, 0, z0), rhino3dm.Point3d(x1, 0.5, z1)
    ))
