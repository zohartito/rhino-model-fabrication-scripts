# What Worked / What Failed

This is the original project's practical trial/error log. Its observations were not reproduced while preparing this reference copy. The model, print files, and physical samples are not included.

## Worked

- Making return surfaces into real closed solids before exporting.
- Return thickness `0.026 in` / `0.66 mm`.
- Envelope panel offset `0.01 in` to match the already-printed South panels.
- Boolean union / joining four sides into one return object, so 24 sides become 6 returns.
- Keeping final STL outputs loud and explicit with `FINAL`, band name, and plate number.
- Restarting Rhino when repeated file-based exports started behaving differently from earlier successful runs.
- Checking object counts before trusting output, especially for corrugated perforated panels.
- Saving workflow notes and scripts separately from heavy Rhino/STL assets.

## Failed Or Misleading

- Treating Bambu "empty first layer" as only a support/raft problem.
- Assuming Drop To Bed would fix zero-thickness/open-surface geometry.
- Using Bambu repair as the main fix when the Rhino topology was bad.
- Laying returns broad-face-down. It looked logical for flatness, but it produced giant support, bad packing, and wrong orientation.
- Creating generic CLT patch boxes. They did not match the existing wall module language.
- Exporting too many plate files into the main model folder without a controlled output folder.
- Trusting one layer of the slicer preview. Layer 1 can make walls look missing when they appear higher up.

## Thin Wall Lesson

`0.01 in` is only `0.254 mm`, thinner than a 0.4 mm nozzle. The printer cannot physically extrude that exact thinness with a 0.4 mm nozzle. But the South panels already used that workflow, so matching the South panel STL and slicer behavior mattered more than theoretical thickness.

For returns, `0.026 in` was the better compromise because those pieces needed enough real wall thickness to slice cleanly.

## Orientation Lesson

The phrase "flat on the outer subframe side" meant:

```text
the outer-subframe-contact edge touches the print bed
```

It did not mean:

```text
lay the biggest broad face flat on the bed
```

That distinction changed the whole print strategy.

## Topology Lesson

Rhino can show "no naked edges" while still reporting non-manifold edges or invalid face loops. If a slicer shows missing middle geometry on only one part, inspect that part in Rhino with:

```text
Check
SelBadObjects
ShowEdges
```

Then rebuild that part from the source surfaces instead of thickening every part.


## How the scripts preserve those attempts

- `trials/rhino_make_printable_real_south_returns_flat_face.py` and `trials/repack_real_south_returns_compact_4plates.py` preserve the broad-face-down mistake. Compacting the layout kept the same unsuitable orientation.
- `trials/repack_real_south_returns_upright_subframe_edge_2plates.py` records the later Y/Z swap to stand those returns upright. The other two trial scripts are diagnostic probes, not finished exporters.
- `rhino-bambu/rhino_batch_make_printable_west_returns.py` also retains a flat-face mapping by default. Its folder and output labels do not make it the final upright method.
- `clt-laser/make_clt_laser_south_west.py` preserves the bounding-box/global-axis projection associated with incorrect window positions. The named-vertex script changes the selection and projection axes but still uses convex hulls. The edge-curve prototype and repacker retain the later method, including its curve-sampling limitations.
- `model-cleanup/fix_macro_bottom_clt_infill.py` preserves the box-patch attempt. `replace_courtyard_clt_infill_with_wall_modules.py` shows the module-based replacement.

The full [script inventory](../scripts/README.md) distinguishes these approaches and their assumptions. No geometry logic was changed for this copy.
