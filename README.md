# Rhino Model Fabrication Notes and Scripts

A reference collection of 23 Python scripts and notes from an undergraduate architecture studio. The work involved making thin facade panels, window returns, and perforated panels into solids for 3D printing, and projecting CLT backup walls into laser-cut sheets.

**The source model is not included. These scripts must be adapted before use.** They depend on one model's layer names, object names, coordinate directions, grouping, dimensions, and scale. They are working-session artifacts, including unsuccessful approaches, rather than a ready-to-run tool or a single pipeline.

## What the folders contain

| Folder | Problem addressed | What is preserved |
| --- | --- | --- |
| `scripts/rhino-bambu/` — 9 scripts | Open return surfaces and thin facade parts needed thickness, grouping, orientation, and plate layouts. | Rhino solid/mesh exporters plus a headless helper that splits West returns by band. Some earlier approaches remain here; this folder does not imply a verified final version. |
| `scripts/clt-laser/` — 4 scripts | CLT walls needed window openings, score lines, and layouts on small plywood stock. | Bounding-box projection, named-vertex projection, edge-curve polygonization, and sheet repacking. These are different attempts with different limitations. |
| `scripts/model-cleanup/` — 5 scripts | Stairs drew poorly in Make2D and some lower CLT wall zones were missing. | An inventory script, box infill, a stair rebuild, module-based infill replacement, and a save/report helper. Several edit or save the active document. |
| `scripts/trials/` — 5 scripts | Failed orientation and uncertain perforated-panel grouping/meshing needed investigation. | Broad-face-down exports and compaction that did not solve the printing problem, diagnostics, and the later upright-orientation correction. They remain together to explain what failed and how the approach changed. |

## Read the failure record first

[What worked and what failed](docs/what-worked-what-failed.md) is the main record of the original work. It describes missing slicer geometry, invalid topology despite closed surfaces, excessive support from broad-face-down returns, and CLT patches that did not match the wall modules. It also records the useful practices: closed solids, count checks, upright returns on the subframe-contact edge, and reviewing more than the first slicer layer.

These are observations recorded during the original project. They were not reproduced while preparing this copy. Names such as `FINAL`, `PRINT_READY`, `EXACT`, and `MATCH_SOUTH_STL` are inherited labels, not current validation results.

## Assumptions that need adapting

- **Selection and grouping:** return scripts often expect four consecutive surfaces per opening. The West splitter requires a particular layer and names ending in a three-digit ID and `sill`, `head`, `jamb_S`, or `jamb_N`. Panel scripts use three specific facade layers. The file named `preserve13` actually sets `BREPS_PER_PANEL = 19`.
- **Units and scale:** the CLT generators and West splitter assume source coordinates in real feet, with `1 ft = 0.5 in = 12.7 mm` at model scale. They do not establish that scale from the source file. Some active-document exporters convert document units to millimeters; several file-based exporters require coordinates already in model millimeters.
- **Thickness:** return scripts use `0.66 mm`. Flat panels use `0.254 mm` (`0.01 in`). Perforated-panel variants compare `0.254 mm` with the inherited `0.5705078125 mm` reference value. The reference STL is absent, so that measurement has not been checked here.
- **Stock and packing:** laser scripts use `12 × 12 in` stock, `0.125 in` margins, `2.5 in` score spacing, and `32 × 18 in` bed guides. Most print exporters use a `250 × 250 mm` packing box with `5 mm` margins and spacing. These are code parameters, not a verified machine setup; some packers still export oversized parts.
- **Coordinates:** panel thickness adjustments assume the thin direction is Z, or X/Z for corrugated ribs. Return orientation mappings and cleanup coordinates are tied to the absent model. Changing paths alone is insufficient.

The [script inventory](scripts/README.md) lists the runtime, exact selection assumptions, units, outputs, and path settings for every script.

## Runtime and preparation

Scripts importing `Rhino`, `scriptcontext`, `rhinoscriptsyntax`, or `System.Drawing` need the Rhino environment; the inventory assigns them to the Rhino 8 script editor with CPython 3. A file-based script that imports `Rhino` still needs RhinoCommon. It is not a standalone `rhino3dm` script.

The seven headless scripts use CPython 3 with `rhino3dm` and/or Shapely, or `trimesh` and NumPy. Dependencies and their versions have not been installed or exercised for this copy. No script was identified as IronPython 2.7-only from its syntax; compatibility with IronPython has not been tested.

Before adapting a script, inspect its selection logic and constants, replace the `path/to/...` settings, and create the needed output/log directories. `DEFAULT_MODEL_PATH` is only a fallback for an unsaved active document; saved documents still supply their own paths, and several scripts export alongside them. Work on a model copy. Cleanup scripts can delete earlier generated objects, hide layers, and save a new file.

## Notes and checklists

- [Return panels](docs/rhino-to-bambu-returns.md)
- [Flat and perforated envelope panels](docs/rhino-to-bambu-envelope-panels.md)
- [CLT laser cutting](docs/clt-laser-cutting.md)
- [Stairs and CLT cleanup](docs/model-cleanup.md)
- [Preflight checklists](docs/checklists/)
- [Workflow run-log template](docs/templates/workflow-run-log.md)

The notes preserve the original fabrication reasoning. They do not supply the absent reference models, STL examples, slicer settings, or a machine profile.

## Validation of this copy

All 23 scripts passed `python3 -m py_compile` with CPython 3.15.2. This checks syntax only: it does not import Rhino or other dependencies, open a model, evaluate geometry, or run any script. No Rhino, slicer, printer, or laser validation was performed. Geometry logic and numerical parameters were retained.

Model files, meshes, print jobs, and other generated fabrication assets are excluded. See [LICENSE](LICENSE) for the MIT license.
