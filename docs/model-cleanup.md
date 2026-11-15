# Rhino Model Cleanup Notes

These are notes from the original project, not results reproduced for this copy. The source model and reference fabrication files are not included. See the [script inventory](../scripts/README.md) for the actual code assumptions.

These scripts and notes cover fixes to the master model, especially the stairs and missing CLT wall infill.

## Stairs / Make2D

Problem:

- Stair stringers looked jagged/wrong in Make2D.
- Some treads/runs were missing.
- The stair read as many disjointed pieces instead of one coherent stair assembly.

Workflow:

1. Use the stair cleanup script on the active model.
2. Keep old layers hidden or preserved rather than deleting blindly.
3. Rebuild the stair as coherent runs, risers, landings, and stringers.
4. Test in Make2D after rebuilding, not only in shaded view.
5. Save the fixed version to a configured output folder when a new saved copy is needed.

## Missing CLT Bottom Walls

Problem:

- Some courtyard/north/bottom wall zones had missing CLT under windows.
- First attempts made boxy patches instead of matching the wall module language above.

Better workflow:

1. Use nearby valid CLT backup wall modules as templates.
2. Extend/duplicate matching wall panels down to the concrete retaining/base condition.
3. Avoid generic boxes unless they are placeholders.
4. Match layer, display color, thickness, and panel rhythm.
5. Edit the open file when requested, without repeatedly saving extra copies.

## File Saving Rule

Distinguish edits to the active document from a saved copy. The stair and box-infill scripts save to `SAVE_AS_PATH`; the courtyard-module replacement edits the open document without saving. The separate save helper writes the requested copy. Configure those paths before use.
