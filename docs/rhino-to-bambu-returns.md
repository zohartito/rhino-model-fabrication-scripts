# Rhino To Bambu: Return Panels

These are notes from the original project, not results reproduced for this copy. The source model and reference fabrication files are not included. See the [script inventory](../scripts/README.md) for the actual code assumptions.

This is the workflow that stabilized the South and West return panels.

## Problem We Solved

The return geometry started as surfaces. Bambu saw empty first layers, missing walls, non-manifold edges, or weird support behavior because the mesh was not always a clean printable solid.

The correct fix was not raft/supports first. The correct fix was making valid solids in Rhino, orienting them consistently, and only then slicing.

## Thickness

Use:

```text
0.026 in = 0.6604 mm
```

This is thick enough for a 0.4 mm nozzle but still visually thin at model scale.

## Rhino Workflow

1. Open the return band `.3dm`.
2. Confirm units.
3. If the file is in millimeters, use `0.66 mm`.
4. If the file is in inches, use `0.026 in`.
5. Each return is usually four sides/pieces. Boolean union/join them into one object per return, so 24 surfaces become 6 return objects.
6. Run `OffsetSrf`.
7. Set `Solid=Yes`.
8. Offset by the correct thickness.
9. Run `Check` on the results.
10. Every final return should report as a valid closed polysurface.
11. Drop all returns so the print-bed contact edge is at Z=0.
12. Export STL in millimeters.

## Critical Orientation

The winning orientation for returns is upright, rotated so the flat print-bed side is the edge that touches the outer subframe.

Do not lay returns broad-face-down. That made huge support, bad packing, and a very different print than the proven South references.

Use the South reference STL orientation when in doubt:

```text
PRINTED 3rd to top south band returns_GROUPED_plate1_FINAL(A1 5h43m P2S 5h17m).stl
```

## Bambu Studio Checks

Before printing:

1. Import STL.
2. Confirm all parts touch the plate.
3. Slice.
4. Scrub the layer slider from bottom to top.
5. At low layers, verify every return has printable geometry.
6. Support should be reasonable and local, not massive.
7. If one part disappears through the middle, go back to Rhino and check that object for topology problems.

## Common Rhino Commands

```text
Check
ShowEdges
SelBadObjects
OffsetSrf
BooleanUnion
Join
Explode
DupEdge
Cap
Move
BoundingBox
```

## When A Solid Says Closed But Still Fails

Closed is not enough. We saw a case with:

```text
no naked edges
non-manifold edges
invalid face loop
```

That means Rhino topology is corrupt even though it looks closed. Rebuild that one return from the original surfaces and offset again.
