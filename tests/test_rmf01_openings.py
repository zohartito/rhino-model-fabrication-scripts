import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import rhino3dm

from tests.support import ROOT, load_script, panel, punched_wall


class OpeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proto = load_script("clt-laser/prototype_exact_clt_laser.py", "rmf01_proto")

    def extract(self, breps, proto=None):
        proto = proto or self.proto
        sources = [proto.SourceObject(i, f"SOUTH_CLT_{i}", "south", brep)
                   for i, brep in enumerate(breps)]
        geometry, diagnostics = proto.polygons_from_brep_edges(sources, (1, 0), 16)
        self.assertEqual(diagnostics["failed"], [])
        return geometry

    def assert_window(self, geometry):
        self.assertAlmostEqual(geometry.area, 21.0)
        self.assertEqual(self.proto.geom_stats(geometry)["interior_hole_rings"], 1)

    def test_rmf01_single_brep_preserves_window(self):
        self.assert_window(self.extract([punched_wall()]))

    def test_rmf01_separate_panels_preserve_window(self):
        self.assert_window(self.extract([
            panel(0, 0, 10, 3), panel(0, 7, 10, 10),
            panel(0, 3, 3, 7), panel(7, 3, 10, 7),
        ]))

    def test_rmf01_other_brep_can_fill_window(self):
        geometry = self.extract([punched_wall(), panel(3, 3, 7, 7)])
        self.assertAlmostEqual(geometry.area, 25.0)
        self.assertEqual(self.proto.geom_stats(geometry)["interior_hole_rings"], 0)

    def test_rmf01_repacker_uses_opening_preserving_extraction(self):
        repacker = load_script("clt-laser/repack_clt_laser_sheets.py", "rmf01_repacker")
        repacker.SOURCE_SCRIPT = ROOT / "scripts/clt-laser/prototype_exact_clt_laser.py"
        self.assert_window(self.extract([punched_wall()], proto=repacker.load_proto()))

    def test_rmf01_cli_manifest_preserves_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = rhino3dm.File3dm()
            layer = rhino3dm.Layer()
            layer.Name = self.proto.CLT_LAYER
            layer_index = model.Layers.Add(layer)
            attrs = rhino3dm.ObjectAttributes()
            attrs.LayerIndex = layer_index
            attrs.Name = "SOUTH_CLT_WINDOW"
            model.Objects.AddBrep(punched_wall(), attrs)
            source = root / "source.3dm"
            self.assertTrue(model.Write(str(source), 8))
            result = subprocess.run([
                sys.executable, "-B", str(ROOT / "scripts/clt-laser/prototype_exact_clt_laser.py"),
                "--source", str(source), "--out-root", str(root / "output"),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifests = list((root / "output").glob("*/prototype*manifest*.json"))
            self.assertEqual(len(manifests), 1)
            south = json.loads(manifests[0].read_text())["side_results"]["south"]
            self.assertAlmostEqual(south["polygon_area_in2"], 21.0)
            self.assertEqual(south["polygon_stats"]["interior_hole_rings"], 1)
            self.assertEqual(south["diagnostics"]["failed"], [])
