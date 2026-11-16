from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import rhino3dm

from tests.support import load_script, panel


class DuplicateReturnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.splitter = load_script("rhino-bambu/split_west_returns_by_band.py", "rmf03_splitter")

    def model(self, names):
        model = rhino3dm.File3dm()
        layer = rhino3dm.Layer()
        layer.Name = self.splitter.RETURN_LAYER
        layer_index = model.Layers.Add(layer)
        for name in names:
            attrs = rhino3dm.ObjectAttributes()
            attrs.LayerIndex = layer_index
            attrs.Name = name
            surface = panel(0, 0, 1, 1).Faces[0].DuplicateFace(False)
            self.assertFalse(surface.IsSolid)
            model.Objects.AddBrep(surface, attrs)
        return model, layer_index

    def run_splitter(self, model, root):
        source = root / "source.3dm"
        self.assertTrue(model.Write(str(source), 8))
        with patch.object(self.splitter, "SOURCE", source), \
                patch.object(self.splitter, "OUT_DIR", root / "bands"), \
                redirect_stdout(io.StringIO()):
            self.splitter.main()

    def test_rmf03_duplicate_key_identifies_both_source_objects(self):
        model, index = self.model(["WEST_RET_001_sill", "WEST_OTHER_001_sill"])
        with self.assertRaisesRegex(
            RuntimeError,
            r"Duplicate West return 001.*sill.*object 0.*WEST_RET_001_sill.*object 1.*WEST_OTHER_001_sill",
        ):
            self.splitter.collect_returns(model, index)

    def test_rmf03_five_surfaces_are_rejected_before_export(self):
        names = ["WEST_RET_001_sill", "WEST_RET_001_sill", "WEST_RET_001_head",
                 "WEST_RET_001_jamb_S", "WEST_RET_001_jamb_N"]
        model, _ = self.model(names)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, r"Duplicate West return 001.*sill.*object 0.*object 1"):
                self.run_splitter(model, root)
            self.assertEqual(list((root / "bands").iterdir()), [])

    def test_rmf03_unique_sides_export_and_unmatched_objects_are_reported(self):
        model, layer_index = self.model([f"WEST_RET_001_{side}" for side in self.splitter.SIDE_ORDER])
        attrs = rhino3dm.ObjectAttributes()
        attrs.LayerIndex = layer_index
        attrs.Name = "unmatched_point"
        model.Objects.AddPoint(rhino3dm.Point3d(0, 0, 0), attrs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_splitter(model, root)
            manifest = json.loads((root / "bands/west_returns_split_by_band_manifest.json").read_text())
            self.assertEqual(manifest["total_source_return_surfaces"], 4)
            self.assertEqual(manifest["total_complete_returns"], 1)
            self.assertEqual(manifest["incomplete_returns"], [])
            self.assertEqual([obj["object_index"] for obj in manifest["unmatched_objects"]], [4])
            self.assertEqual(manifest["bands"][0]["surface_count"], 4)
            exported = rhino3dm.File3dm.Read(manifest["bands"][0]["file"])
            self.assertEqual(exported.Settings.ModelUnitSystem, rhino3dm.UnitSystem.Millimeters)
            self.assertEqual([obj.Attributes.Name for obj in exported.Objects], [
                f"WEST_RETURN_BAND_01_RID001_{side}" for side in self.splitter.SIDE_ORDER
            ])
