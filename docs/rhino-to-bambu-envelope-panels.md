# Rhino To Bambu: Envelope Panels

These are notes from the original project, not results reproduced for this copy. The source model and reference fabrication files are not included. See the [script inventory](../scripts/README.md) for the actual code assumptions.

This covers the flat/corrugated, solid/perforated facade panels, especially the West panels that needed to match the earlier South print settings.

## Key Difference From Returns

Returns used `0.026 in`.

Envelope panels should match the South panel reference and use:

```text
OffsetSrf distance = 0.01 in
0.01 in = 0.254 mm
```

This was intentional because the South facade panels were already printed from that setup.

## South Reference Files

Use these as visual and thickness references:

```text
flat solid 2nd to top band.stl
flat perf band south top band print.stl
```

## Workflow

1. Open the West panel `.3dm`.
2. Confirm units.
3. If the original South method was done in inches, use the same Rhino operation logic: `OffsetSrf` with distance `0.01`.
4. Keep panel grouping consistent with the South files.
5. For perforated/corrugated panels, preserve the per-panel object grouping. Some corrugated perforated bands had 13 polysurfaces per panel.
6. Export final print STLs with names that identify:
   - facade side
   - band position
   - flat/corr
   - solid/perf
   - final/plate number

## Count Sanity Checks

Use object counts as a red flag check. For example:

```text
611 polysurfaces / 13 per panel = 47 panels
```

If the count does not divide cleanly, pause and inspect before exporting.

## Bambu Checks

1. Import the STL.
2. Confirm it visually matches the South reference.
3. Confirm panel count.
4. Slice with the same Bambu logic used for South.
5. Scrub layers.
6. Avoid letting Bambu create extra/new plates in the main model folder by using controlled output folders.

## What To Watch

- If the file suddenly fails after many successful runs, restart Rhino before assuming the geometry is bad.
- If the script creates too many files, check output path logic before continuing.
- For corrugated perforated panels, the grouping and object count matter more than it feels like they should.
