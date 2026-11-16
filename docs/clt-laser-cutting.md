# CLT Laser Cutting Workflow

These are notes from the original project, not results reproduced for this copy. The source model and reference fabrication files are not included. See the [script inventory](../scripts/README.md) for the actual code assumptions.

This records the CLT wall laser-cutting workflow for the South and West walls.

## Stock And Scale

Material purchased:

```text
12 x 12 x 1/8 in Baltic birch plywood
about 3 mm thick
```

Model scale:

```text
1 ft real = 1/2 in model
5 ft real = 2.5 in model
```

The teacher's "score every 5 ft" instruction means scoring every `2.5 in` on the physical model.

## Laser Drawing Colors

Use:

```text
Red = cut
Blue = score
Yellow = material / laser bed perimeter
```

Confirm the local laser cutter settings before cutting; this repo keeps the drafting convention, not a guaranteed machine profile.

## Design Intent

The CLT sheets should represent the wall layer with window openings punched out. Avoid creating many tiny fragments unless the stock size forces it.

Use the largest possible 12 x 12 in pieces and pack intelligently. Some pieces can share a sheet if there is enough unused plywood area.

## Workflow

1. Work from the corrected master model.
2. Isolate CLT backup wall layers for South and West.
3. Confirm window holes match the actual model, not an older draft.
4. Project/flatten the wall faces into laser-cut geometry.
5. Cut red outlines and window holes.
6. Score blue CLT seams at 5 ft real increments.
7. Add yellow 12 x 12 in stock outlines.
8. Label sheet positions and panel positions so assembly is obvious.

## Important Lesson

The first laser export had suspicious window-hole positions. When visual intuition and the exported holes disagree, trust the model view and re-check object selection, face direction, and coordinate mapping.

The later workflow used a corrected master model. That model is not included. Set the source path to your own copy, for example:

```text
path/to/your-model.3dm
```

Keep generated cut files in a separate output folder and verify which model revision supplied them.

The edge-curve prototype creates a new private run directory under `--out-root`
with a timestamp and random suffix. It never reuses a previous run directory;
its SVG writer rejects existing paths, including symlinks, instead of replacing
them. Use the output path printed at the end of the run.
