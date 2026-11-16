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

## RMF-02: document insertion units

`tests.test_rmf02_document_units.DocumentUnitTests` executes each exporter's
actual packing functions, document-insertion loop, and 3DM writer with a
rhino3dm geometry adapter and an in-memory document sink. It never imports
the active-document scripts or RhinoCommon. Both inch and foot cases failed
before the fix (25.4x and 304.8x scale errors); both millimeter controls passed.

Both exporters now duplicate packed solids and convert those copies from mm
back to document units before insertion. All six cases pass, including physical
dimensions and placement, unmodified source/packed geometry, and reading back
plate 3DM files with mm units and unchanged coordinates. The STL mesher still
receives the same packed mm solids. Live Rhino insertion and STL meshing were
not exercised.

## RMF-03: duplicate return sides

`tests.test_rmf03_duplicate_returns.DuplicateReturnTests` checks both collection
and the complete headless splitter on a five-surface input with two sill objects
sharing one return ID. Both rejection tests failed before the fix: no error was
raised. The four-unique-side export and unmatched-object control passed.

Collection now rejects a duplicate `(return ID, side)` before overwriting the
first object or writing band files. The error identifies both object-table
indices and names, even when different names parse to the same key. All three
tests pass; the valid case still writes four ordered surfaces in a mm 3DM and
reports unmatched objects in its manifest.

## RMF-04: exclusive SVG creation and private run directories

`tests.test_rmf04_output_safety.OutputSafetyTests` reproduced target truncation
through both a direct SVG symlink and a symlink inside a precreated timestamp
directory. Dangling links created their targets, existing files were replaced,
and two runs in the same second reused one directory. Those five tests failed;
the normal cut/hole/score SVG control passed.

Each prototype run now creates a unique private directory (`0700`) with a
random suffix after the timestamp. SVG creation is exclusive (`O_EXCL` with
`O_CREAT`, plus `O_NOFOLLOW` where available) and uses mode `0600`. Existing files
and symlinks raise an OS error without changing their targets. All six tests
pass, including normal SVG output and separate complete outputs for same-second
runs. This protects the shared-output-directory case; it does not promise
isolation from another process running as the same user.
