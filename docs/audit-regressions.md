# Targeted audit regressions

These tests address RMF-01 through RMF-04 using synthetic data only. They do not
validate the absent source model, RhinoCommon execution, or fabrication hardware.
No desktop application is imported or contacted. Test artifacts belong in a
scratch directory outside the repository; set `TMPDIR` to that directory.

Use CPython 3 with the already documented `rhino3dm` and Shapely dependencies
installed in an external virtual environment. From the repository root:

```sh
python -I -B -m unittest discover -s tests -t . -v
```

Baseline before any changes: unittest discovery found 0 tests (0 passed,
0 failed, 0 skipped); all 23 scripts compiled successfully without importing
them. Audit tests use CPython 3.12, rhino3dm 8.35.0 and Shapely 2.2.0.

## RMF-01: openings within one Brep

`tests.test_rmf01_openings.OpeningTests` reproduces a valid, closed 10 x 10 ft
wall with one 4 x 4 ft through-window (10 faces, 24 edges). Before the fix,
the single-Brep, CLI-manifest, and repacker-extraction tests each returned
25 in² instead of 21 in². The separate-panel and separate-infill controls passed.

The extractor now uses even/odd nesting depth to distinguish material cells
from opening cells within each Brep before unioning different Breps. All five
tests pass; the punched wall has 21 in² and one interior ring. Material from
a separate Brep can still fill the opening. This is projected-edge reasoning,
not a general solid projection: nested outlines are assumed to be openings,
and recessed faces or complex overlapping projections still require review.
