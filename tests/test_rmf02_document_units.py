"""Execute packing, insertion, and file-writing code without importing Rhino.

The scripts run against the active document on import. Extract their function
definitions and insertion loop instead; adapt only the RhinoCommon API boundary
to rhino3dm geometry and an in-memory document sink.
"""

import ast
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import rhino3dm as r

from tests.support import ROOT


class BrepAdapter:
    def __init__(self, geometry):
        self.geometry = geometry

    def DuplicateBrep(self):
        return BrepAdapter(self.geometry.Duplicate())

    def GetBoundingBox(self, accurate):
        return self.geometry.GetBoundingBox()

    def Transform(self, transform):
        return self.geometry.Transform(transform)


class Point3d:
    Origin = r.Point3d(0, 0, 0)

    def __new__(cls, *args):
        return r.Point3d(*args)


class FileAdapter:
    def __init__(self):
        self.model = r.File3dm()
        self.Settings = self.model.Settings
        self.Objects = SimpleNamespace(AddBrep=self.add_brep)

    def add_brep(self, brep, attrs):
        return self.model.Objects.AddBrep(brep.geometry, attrs)

    def Write(self, path, version):
        return self.model.Write(path, version)


def bounds(geometry):
    bb = geometry.GetBoundingBox()
    return (bb.Min.X, bb.Min.Y, bb.Min.Z, bb.Max.X, bb.Max.Y, bb.Max.Z)


class DocumentUnitTests(unittest.TestCase):
    def check_insertion(self, script, units, mm_per_unit):
        path = ROOT / "scripts/rhino-bambu" / script
        tree = ast.parse(path.read_text(), filename=str(path))
        inserted = []
        scales = {r.UnitSystem.Millimeters: 1.0, r.UnitSystem.Inches: 25.4,
                  r.UnitSystem.Feet: 304.8}
        doc = SimpleNamespace(
            ModelUnitSystem=units,
            Objects=SimpleNamespace(AddBrep=lambda brep, attrs: inserted.append(brep.geometry.Duplicate())),
        )
        namespace = {
            "math": math,
            "sc": SimpleNamespace(doc=doc),
            "Rhino": SimpleNamespace(
                UnitSystem=r.UnitSystem,
                RhinoMath=SimpleNamespace(UnitScale=lambda source, target: scales[source] / scales[target]),
                Geometry=SimpleNamespace(Transform=r.Transform, Point3d=Point3d,
                                         Vector3d=SimpleNamespace(ZAxis=r.Vector3d(0, 0, 1))),
                DocObjects=SimpleNamespace(ObjectAttributes=r.ObjectAttributes),
                FileIO=SimpleNamespace(File3dm=FileAdapter),
            ),
        }
        for node in tree.body:
            if isinstance(node, ast.Assign):
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        namespace[target.id] = value
        definitions = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[])
        exec(compile(definitions, str(path), "exec"), namespace)
        namespace["log"] = lambda message: None

        geometry = r.Brep.CreateFromBoundingBox(r.BoundingBox(
            r.Point3d(5 / mm_per_unit, 8 / mm_per_unit, 3 / mm_per_unit),
            r.Point3d(55.8 / mm_per_unit, 33.4 / mm_per_unit, 3.66 / mm_per_unit),
        ))
        source_bounds = bounds(geometry)
        plates = namespace["pack_into_plates"]([BrepAdapter(geometry)])
        self.assertEqual(len(plates), 1)
        plate = plates[0]
        packed_bounds = bounds(plate["solids"][0].geometry)
        self.assertAlmostEqual(packed_bounds[3] - packed_bounds[0], 50.8)
        self.assertAlmostEqual(packed_bounds[4] - packed_bounds[1], 25.4)
        self.assertAlmostEqual(packed_bounds[5] - packed_bounds[2], 0.66)

        insertion_loops = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name)
            and node.target.id == "solid"
            and any(isinstance(child, ast.Call)
                    and ast.unparse(child.func) == "sc.doc.Objects.AddBrep"
                    for child in ast.walk(node))
        ]
        self.assertEqual(len(insertion_loops), 1)
        namespace.update(plate=plate, attrs=r.ObjectAttributes())
        exec(compile(ast.Module(body=insertion_loops, type_ignores=[]), str(path), "exec"), namespace)
        self.assertEqual(len(inserted), 1)
        for actual, expected in zip(bounds(inserted[0]), packed_bounds):
            self.assertAlmostEqual(actual * mm_per_unit, expected)
        self.assertEqual(bounds(geometry), source_bounds)
        self.assertEqual(bounds(plate["solids"][0].geometry), packed_bounds)

        # Insertion must leave the packed solids in mm for the existing exporter.
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "plate.3dm")
            if "write_plate_3dm" in namespace:
                namespace["write_plate_3dm"](plate, output)
            else:
                namespace["write_3dm"](output, plate["solids"], "test_panel")
            exported = r.File3dm.Read(output)
            self.assertIsNotNone(exported)
            self.assertEqual(exported.Settings.ModelUnitSystem, r.UnitSystem.Millimeters)
            self.assertEqual(len(exported.Objects), 1)
            for actual, expected in zip(bounds(exported.Objects[0].Geometry), packed_bounds):
                self.assertAlmostEqual(actual, expected)

    def test_rmf02_returns_inches(self):
        self.check_insertion("rhino_make_printable_current_returns.py", r.UnitSystem.Inches, 25.4)

    def test_rmf02_returns_feet(self):
        self.check_insertion("rhino_make_printable_current_returns.py", r.UnitSystem.Feet, 304.8)

    def test_rmf02_returns_millimeters(self):
        self.check_insertion("rhino_make_printable_current_returns.py", r.UnitSystem.Millimeters, 1.0)

    def test_rmf02_flat_panels_inches(self):
        self.check_insertion("rhino_make_printable_flat_panels_current.py", r.UnitSystem.Inches, 25.4)

    def test_rmf02_flat_panels_feet(self):
        self.check_insertion("rhino_make_printable_flat_panels_current.py", r.UnitSystem.Feet, 304.8)

    def test_rmf02_flat_panels_millimeters(self):
        self.check_insertion("rhino_make_printable_flat_panels_current.py", r.UnitSystem.Millimeters, 1.0)
