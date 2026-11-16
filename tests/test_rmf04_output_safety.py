from argparse import Namespace
from contextlib import redirect_stdout
import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import rhino3dm
from shapely.geometry import LineString, box

from tests.support import load_script, punched_wall


class OutputSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proto = load_script("clt-laser/prototype_exact_clt_laser.py", "rmf04_proto")

    def sheet(self):
        return {"side": "south", "row": 1, "col": 1,
                "piece": box(0, 0, 5, 5).difference(box(1.5, 1.5, 3.5, 3.5)),
                "scores": [LineString([(1, 0), (1, 5)])]}

    def attempt_write(self, path):
        try:
            self.proto.write_sheet_svg(str(path), self.sheet())
        except OSError as error:
            return error
        return None

    def test_rmf04_symlink_target_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim.txt"
            victim.write_text("synthetic victim\n")
            output = root / "sheet.svg"
            output.symlink_to(victim)
            error = self.attempt_write(output)
            self.assertEqual(victim.read_text(), "synthetic victim\n")
            self.assertTrue(output.is_symlink())
            self.assertIsInstance(error, OSError)

    def test_rmf04_dangling_symlink_target_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "missing.txt"
            output = root / "sheet.svg"
            output.symlink_to(target)
            error = self.attempt_write(output)
            self.assertFalse(target.exists())
            self.assertTrue(output.is_symlink())
            self.assertIsInstance(error, OSError)

    def test_rmf04_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sheet.svg"
            output.write_text("prior export\n")
            error = self.attempt_write(output)
            self.assertEqual(output.read_text(), "prior export\n")
            self.assertIsInstance(error, OSError)

    def test_rmf04_new_svg_contains_cut_hole_and_score(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sheet.svg"
            self.proto.write_sheet_svg(str(output), self.sheet())
            paths = ET.parse(output).getroot().findall("{http://www.w3.org/2000/svg}path")
            self.assertEqual([path.attrib["stroke"] for path in paths], ["red", "red", "blue"])

    def run_prototype(self, root):
        source = root / "source.3dm"
        if not source.exists():
            model = rhino3dm.File3dm()
            layer = rhino3dm.Layer()
            layer.Name = self.proto.CLT_LAYER
            index = model.Layers.Add(layer)
            attrs = rhino3dm.ObjectAttributes()
            attrs.LayerIndex = index
            attrs.Name = "SOUTH_CLT_WINDOW"
            model.Objects.AddBrep(punched_wall(), attrs)
            self.assertTrue(model.Write(str(source), 8))
        args = Namespace(source=str(source), out_root=str(root / "output"),
                         layer=self.proto.CLT_LAYER, samples_per_span=16)
        with patch.object(self.proto, "parse_args", return_value=args), \
                patch.object(self.proto.time, "strftime", return_value="20000101_000000"), \
                redirect_stdout(io.StringIO()):
            self.proto.main()

    def test_rmf04_precreated_timestamp_directory_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim.txt"
            victim.write_text("synthetic victim\n")
            prepared = root / "output/prototype_exact_clt_laser_output_20000101_000000"
            (prepared / "south_svg_sheets").mkdir(parents=True)
            output = prepared / "south_svg_sheets/south_clt_sheet_01_R1_C1.svg"
            output.symlink_to(victim)
            self.run_prototype(root)
            self.assertEqual(victim.read_text(), "synthetic victim\n")
            self.assertEqual(list(prepared.iterdir()), [prepared / "south_svg_sheets"])
            self.assertEqual(len(list((root / "output").iterdir())), 2)

    def test_rmf04_same_second_runs_use_distinct_private_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_prototype(root)
            self.run_prototype(root)
            runs = list((root / "output").iterdir())
            self.assertEqual(len(runs), 2)
            for run in runs:
                self.assertEqual(stat.S_IMODE(run.stat().st_mode), 0o700)
                self.assertEqual(len(list(run.glob("*.3dm"))), 1)
                self.assertEqual(len(list(run.glob("*manifest*.json"))), 1)
                self.assertEqual(len(list(run.glob("south_svg_sheets/*.svg"))), 1)
