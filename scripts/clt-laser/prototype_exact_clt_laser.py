#!/usr/bin/env python3
"""
Prototype exact-ish CLT laser extraction from the source Rhino file.

This prototype writes to the configured output root. Set SOURCE and OUT_ROOT
or supply --source and --out-root before adapting it to another model.

Approach:
  1. Read CLT Breps from the named CLT layer.
  2. Use SOUTH_CLT_* and WEST_CLT_* object names to split sides.
  3. Derive each side's local horizontal elevation axis from long Brep edges.
  4. Project Brep edge curves to side elevation and polygonize those curves.
  5. Tile the resulting wall polygons onto 12x12 stock at 1 ft = 0.5 in.

rhino3dm caveat:
  rhino3dm exposes Brep faces and edges, but not Brep loops/trims or RhinoCommon
  curve/solid boolean helpers. This prototype therefore polygonizes projected
  Brep edge curves with Shapely. For truly exact NURBS/trim extraction inside
  Rhino, see RHINOCOMMON_NOTES in the manifest output.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence
from xml.sax.saxutils import escape

import rhino3dm
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Polygon, box
from shapely.ops import polygonize, unary_union


SOURCE = "path/to/your-model.3dm"
CLT_LAYER = "20_CLT_THICK_REMAP_49FT_BACKUP_WALL_V68"

OUT_ROOT = "path/to/laser-output"

# Physical model scale: 1 real foot = 1/2 model inch.
SCALE_IN_PER_FT = 0.5

# Teacher's CLT factory-panel module: 5 real ft = 2.5 model inches.
SCORE_SPACING_IN = 2.5

STOCK_W_IN = 12.0
STOCK_H_IN = 12.0
STOCK_MARGIN_IN = 0.125
WORK_W_IN = STOCK_W_IN - 2 * STOCK_MARGIN_IN
WORK_H_IN = STOCK_H_IN - 2 * STOCK_MARGIN_IN

BED_W_IN = 32.0
BED_H_IN = 18.0
BED_GAP_IN = 2.0

MIN_SEGMENT_IN = 0.001
MIN_PANEL_AREA_IN2 = 0.0001

RHINOCOMMON_NOTES = """
If rhino3dm polygonization is not sufficiently exact, run this inside Rhino
Python/RhinoCommon instead:

  - Open the file with Rhino.RhinoDoc.OpenHeadless or use the active document.
  - Find the layer by full path/name and filter Rhino.DocObjects.BrepObject where
    Attributes.Name starts with SOUTH_CLT_ or WEST_CLT_.
  - For each side, derive a side Plane with XAxis along the facade direction and
    YAxis = Vector3d.ZAxis.
  - For each Brep, duplicate the display/exterior face loops directly:
      BrepFace.DuplicateFace(False)
      face.Loops, loop.Trims, trim.Edge.EdgeCurve
      Curve.PullToBrepFace / Curve.ProjectToPlane as needed
    or create side elevation contours with:
      Brep.CreateContourCurves(brep, plane)
      Curve.JoinCurves(curves, tolerance)
      Curve.PlanarClosedCurveRelationship / Curve.CreateBooleanUnion
  - Transform curves from the side plane to WorldXY with Transform.PlaneToPlane.
  - Use AreaMassProperties/PlanarClosedCurveRelationship to classify outer loops
    versus holes before writing laser curves.

The RhinoCommon path can preserve exact trims and NURBS. rhino3dm cannot expose
BrepLoop/BrepTrim objects or run Rhino's curve boolean/projection commands.
""".strip()


@dataclass(frozen=True)
class SourceObject:
    index: int
    name: str
    side: str
    geometry: object


def log(message: str) -> None:
    print(message, flush=True)


def layer_indices_by_name(model: rhino3dm.File3dm, layer_name: str) -> list[int]:
    return [
        index
        for index, layer in enumerate(model.Layers)
        if layer.Name == layer_name or layer.FullPath.endswith(layer_name)
    ]


def side_from_name(name: str) -> str | None:
    if name.startswith("SOUTH_CLT_"):
        return "south"
    if name.startswith("WEST_CLT_"):
        return "west"
    return None


def collect_source_objects(model: rhino3dm.File3dm, layer_indices: set[int]) -> list[SourceObject]:
    rows: list[SourceObject] = []
    for index, obj in enumerate(model.Objects):
        if obj.Attributes.LayerIndex not in layer_indices:
            continue
        name = obj.Attributes.Name or ""
        side = side_from_name(name)
        if side is None:
            continue
        rows.append(SourceObject(index=index, name=name, side=side, geometry=obj.Geometry))
    return rows


def point_tuple(point) -> tuple[float, float, float]:
    return (float(point.X), float(point.Y), float(point.Z))


def dominant_edge_axis(breps: Sequence[SourceObject], prefer: tuple[float, float]) -> tuple[float, float]:
    # Weighted double-angle average; directionless edges at theta and theta+pi
    # contribute the same axis.
    c2 = 0.0
    s2 = 0.0
    total = 0.0
    for item in breps:
        brep = item.geometry
        if type(brep).__name__ != "Brep":
            continue
        for edge_index in range(len(brep.Edges)):
            edge = brep.Edges[edge_index]
            p0 = edge.PointAtStart
            p1 = edge.PointAtEnd
            dx = float(p1.X - p0.X)
            dy = float(p1.Y - p0.Y)
            dz = float(p1.Z - p0.Z)
            plan_len = math.hypot(dx, dy)
            if plan_len < 0.75 or abs(dz) > 0.02:
                continue
            theta = math.atan2(dy, dx)
            weight = plan_len
            c2 += math.cos(2.0 * theta) * weight
            s2 += math.sin(2.0 * theta) * weight
            total += weight
    if total <= 0.0:
        return prefer
    theta = 0.5 * math.atan2(s2, c2)
    axis = (math.cos(theta), math.sin(theta))
    if axis[0] * prefer[0] + axis[1] * prefer[1] < 0:
        axis = (-axis[0], -axis[1])
    return axis


def project_point_to_elevation(point, axis: tuple[float, float]) -> tuple[float, float]:
    x, y, z = point_tuple(point)
    s = x * axis[0] + y * axis[1]
    return (s * SCALE_IN_PER_FT, z * SCALE_IN_PER_FT)


def curve_points(curve, samples_per_span: int) -> tuple[list[object], bool]:
    if curve.IsLinear():
        return [curve.PointAtStart, curve.PointAtEnd], True

    try:
        polyline = curve.TryGetPolyline()
    except Exception:
        polyline = None

    if polyline is not None and type(polyline).__name__ == "Polyline" and polyline.Count >= 2:
        return [polyline[i] for i in range(polyline.Count)], True

    domain = curve.Domain
    span_count = max(1, int(curve.SpanCount))
    sample_count = max(2, span_count * samples_per_span + 1)
    points = []
    for i in range(sample_count):
        t = domain.T0 + (domain.T1 - domain.T0) * i / (sample_count - 1)
        points.append(curve.PointAt(t))
    return points, False


def rounded_points(points: Iterable[tuple[float, float]], digits: int = 6) -> list[tuple[float, float]]:
    return [(round(float(x), digits), round(float(y), digits)) for x, y in points]


def projected_edge_lines(
    brep: rhino3dm.Brep,
    axis: tuple[float, float],
    samples_per_span: int,
) -> tuple[list[LineString], int]:
    lines: list[LineString] = []
    approximated = 0
    for edge_index in range(len(brep.Edges)):
        edge = brep.Edges[edge_index]
        points3d, exact_polyline = curve_points(edge, samples_per_span=samples_per_span)
        if not exact_polyline:
            approximated += 1
        points2d = rounded_points(project_point_to_elevation(point, axis) for point in points3d)
        deduped = [points2d[0]]
        for point in points2d[1:]:
            if point != deduped[-1]:
                deduped.append(point)
        if len(deduped) < 2:
            continue
        line = LineString(deduped)
        if line.length >= MIN_SEGMENT_IN:
            lines.append(line)
    return lines, approximated


def polygons_from_brep_edges(
    side_objects: Sequence[SourceObject],
    axis: tuple[float, float],
    samples_per_span: int,
) -> tuple[object, dict]:
    panel_polys = []
    failed = []
    approximated_edges = 0
    polygon_counts = collections.Counter()

    for item in side_objects:
        if type(item.geometry).__name__ != "Brep":
            continue
        lines, approx_count = projected_edge_lines(item.geometry, axis, samples_per_span)
        approximated_edges += approx_count
        if not lines:
            failed.append((item.index, item.name, "no projected lines"))
            continue
        merged_lines = unary_union(lines)
        polygons = [poly for poly in polygonize(merged_lines) if poly.area >= MIN_PANEL_AREA_IN2]
        polygon_counts[len(polygons)] += 1
        if not polygons:
            failed.append((item.index, item.name, "polygonize produced no area"))
            continue
        # polygonize also emits each opening as a separate polygon. Classify
        # nested regions within this Brep before unioning material across Breps.
        # Even nesting depth is material; odd depth is an opening.
        shells = [Polygon(poly.exterior) for poly in polygons]
        for poly in polygons:
            point = poly.representative_point()
            depth = sum(shell.contains(point) for shell in shells) - 1
            if depth % 2 == 0:
                panel_polys.append(poly)

    if not panel_polys:
        return GeometryCollection(), {
            "failed": failed,
            "approximated_edges": approximated_edges,
            "polygon_counts": dict(polygon_counts),
        }

    merged = unary_union(panel_polys).buffer(0)
    minx, miny, _, _ = merged.bounds
    normalized = affinity.translate(merged, xoff=-minx, yoff=-miny)
    return normalized, {
        "failed": failed,
        "approximated_edges": approximated_edges,
        "polygon_counts": dict(polygon_counts),
        "panel_polygon_count": len(panel_polys),
    }


def polygon_iter(geom) -> Iterator:
    if geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield geom
    elif geom.geom_type == "MultiPolygon":
        for part in geom.geoms:
            yield part
    elif geom.geom_type == "GeometryCollection":
        for part in geom.geoms:
            yield from polygon_iter(part)


def line_iter(geom) -> Iterator[LineString]:
    if geom.is_empty:
        return
    if geom.geom_type == "LineString":
        yield geom
    elif geom.geom_type == "MultiLineString":
        for part in geom.geoms:
            yield part
    elif geom.geom_type == "GeometryCollection":
        for part in geom.geoms:
            yield from line_iter(part)


def geom_stats(geom) -> dict:
    polygons = list(polygon_iter(geom))
    bounds = geom.bounds if not geom.is_empty else (0.0, 0.0, 0.0, 0.0)
    return {
        "polygon_components": len(polygons),
        "interior_hole_rings": sum(len(poly.interiors) for poly in polygons),
        "bounds_in": tuple(float(value) for value in bounds),
    }


def ring_points(coords) -> list[tuple[float, float]]:
    pts = [(float(x), float(y)) for x, y in coords]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def side_sheets(side_name: str, poly) -> tuple[list[dict], tuple[float, float, int, int]]:
    width = poly.bounds[2] - poly.bounds[0]
    height = poly.bounds[3] - poly.bounds[1]
    cols = int(math.ceil(width / WORK_W_IN))
    rows = int(math.ceil(height / WORK_H_IN))
    sheets = []
    for row in range(rows):
        for col in range(cols):
            x0 = col * WORK_W_IN
            y0 = row * WORK_H_IN
            tile = box(x0, y0, min(x0 + WORK_W_IN, width), min(y0 + WORK_H_IN, height))
            piece = poly.intersection(tile)
            if piece.is_empty or piece.area < 0.01:
                continue
            piece_local = affinity.translate(piece, xoff=-x0 + STOCK_MARGIN_IN, yoff=-y0 + STOCK_MARGIN_IN)

            score_segments = []
            score_x = SCORE_SPACING_IN
            while score_x < width - 0.001:
                line = LineString([(score_x, -1.0), (score_x, height + 1.0)])
                seg = line.intersection(poly).intersection(tile)
                for ln in line_iter(seg):
                    if ln.length > 0.05:
                        score_segments.append(
                            affinity.translate(ln, xoff=-x0 + STOCK_MARGIN_IN, yoff=-y0 + STOCK_MARGIN_IN)
                        )
                score_x += SCORE_SPACING_IN

            sheets.append(
                {
                    "side": side_name,
                    "row": row + 1,
                    "col": col + 1,
                    "cols": cols,
                    "rows": rows,
                    "piece": piece_local,
                    "scores": score_segments,
                }
            )
    return sheets, (width, height, cols, rows)


def layer(model: rhino3dm.File3dm, name: str, color: tuple[int, int, int, int]) -> int:
    item = rhino3dm.Layer()
    item.Name = name
    item.Color = color
    return model.Layers.Add(item)


def attrs(layer_id: int, name: str | None = None) -> rhino3dm.ObjectAttributes:
    attr = rhino3dm.ObjectAttributes()
    attr.LayerIndex = layer_id
    if name:
        attr.Name = name
    return attr


def add_polyline(model: rhino3dm.File3dm, points: Sequence[tuple[float, float]], layer_id: int, name: str | None = None) -> None:
    if len(points) < 2:
        return
    pl = rhino3dm.Polyline()
    for x, y in points:
        pl.Add(float(x), float(y), 0.0)
    model.Objects.AddPolyline(pl, attrs(layer_id, name))


def add_rect(model: rhino3dm.File3dm, x: float, y: float, w: float, h: float, layer_id: int, name: str | None = None) -> None:
    add_polyline(model, [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], layer_id, name)


def add_label(model: rhino3dm.File3dm, text: str, x: float, y: float, layer_id: int) -> None:
    model.Objects.AddTextDot(text, rhino3dm.Point3d(float(x), float(y), 0.0), attrs(layer_id, text))


def transform_points(points: Sequence[tuple[float, float]], ox: float, oy: float) -> list[tuple[float, float]]:
    return [(x + ox, y + oy) for x, y in points]


def svg_path(points: Sequence[tuple[float, float]]) -> str:
    if not points:
        return ""
    parts = ["M {:.4f} {:.4f}".format(points[0][0], STOCK_H_IN - points[0][1])]
    for x, y in points[1:]:
        parts.append("L {:.4f} {:.4f}".format(x, STOCK_H_IN - y))
    return " ".join(parts)


def write_sheet_svg(path: str, sheet: dict) -> None:
    paths = []
    for poly in polygon_iter(sheet["piece"]):
        paths.append(("red", ring_points(poly.exterior.coords)))
        for interior in poly.interiors:
            paths.append(("red", ring_points(interior.coords)))
    for score in sheet["scores"]:
        paths.append(("blue", [(float(x), float(y)) for x, y in score.coords]))

    label = "{} R{}C{}".format(sheet["side"].upper(), sheet["row"], sheet["col"])
    # Exclusive creation also refuses symlinks where O_NOFOLLOW is unavailable.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<svg xmlns="http://www.w3.org/2000/svg" width="12in" height="12in" viewBox="0 0 12 12">\n')
        f.write('<rect x="0" y="0" width="12" height="12" fill="none" stroke="#ffcc00" stroke-width="0.003"/>\n')
        f.write('<text x="0.18" y="0.35" font-size="0.18" fill="black">{}</text>\n'.format(escape(label)))
        for color, pts in paths:
            f.write('<path d="{}" fill="none" stroke="{}" stroke-width="0.003"/>\n'.format(svg_path(pts), color))
        f.write("</svg>\n")


def write_master_3dm(path: str, sheets: Sequence[dict]) -> None:
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Inches
    cut = layer(model, "CUT_RED_FROM_BREP_EDGES", (255, 0, 0, 255))
    score = layer(model, "SCORE_BLUE_5FT_MODULE", (0, 0, 255, 255))
    bed = layer(model, "LASER_BED_32x18_YELLOW", (255, 220, 0, 255))
    stock = layer(model, "MATERIAL_12x12_YELLOW", (255, 220, 0, 255))
    labels = layer(model, "LABELS_BLACK_DO_NOT_CUT", (0, 0, 0, 255))

    drawn_beds = set()
    for i, sheet in enumerate(sheets):
        bed_index = i // 2
        slot = i % 2
        bed_x = 0.0
        bed_y = bed_index * (BED_H_IN + BED_GAP_IN)
        stock_x = bed_x + 0.5 + slot * 12.75
        stock_y = bed_y + 0.5
        if bed_index not in drawn_beds:
            add_rect(model, bed_x, bed_y, BED_W_IN, BED_H_IN, bed, "laser_bed_{:02d}".format(bed_index + 1))
            drawn_beds.add(bed_index)
        add_rect(model, stock_x, stock_y, STOCK_W_IN, STOCK_H_IN, stock, "{}_R{}C{}".format(sheet["side"], sheet["row"], sheet["col"]))
        add_label(
            model,
            "{} sheet {} R{} C{}".format(sheet["side"].upper(), i + 1, sheet["row"], sheet["col"]),
            stock_x + 0.2,
            stock_y + 0.25,
            labels,
        )
        for poly in polygon_iter(sheet["piece"]):
            add_polyline(model, transform_points(ring_points(poly.exterior.coords), stock_x, stock_y), cut, "cut_outer")
            for interior in poly.interiors:
                add_polyline(model, transform_points(ring_points(interior.coords), stock_x, stock_y), cut, "cut_window_or_hole")
        for seg in sheet["scores"]:
            add_polyline(model, transform_points([(float(x), float(y)) for x, y in seg.coords], stock_x, stock_y), score, "score_5ft")

    model.Write(path, 8)


def layer_inventory(model: rhino3dm.File3dm, layer_indices: Sequence[int]) -> list[dict]:
    rows = []
    for layer_index in layer_indices:
        layer_obj = model.Layers[layer_index]
        counter = collections.Counter()
        for obj in model.Objects:
            if obj.Attributes.LayerIndex == layer_index:
                side = side_from_name(obj.Attributes.Name or "") or "other"
                counter[(type(obj.Geometry).__name__, side)] += 1
        rows.append(
            {
                "index": layer_index,
                "name": layer_obj.Name,
                "full_path": layer_obj.FullPath,
                "visible": bool(layer_obj.Visible),
                "locked": bool(layer_obj.Locked),
                "objects": {"{}:{}".format(k[0], k[1]): v for k, v in sorted(counter.items())},
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype exact-ish CLT laser extraction from Brep edge curves.")
    parser.add_argument("--source", default=SOURCE)
    parser.add_argument("--out-root", default=OUT_ROOT)
    parser.add_argument("--layer", default=CLT_LAYER)
    parser.add_argument("--samples-per-span", type=int, default=16)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(args.out_root, exist_ok=True)
    out_dir = tempfile.mkdtemp(
        prefix="prototype_exact_clt_laser_output_{}_".format(stamp), dir=args.out_root
    )

    log("Reading 3dm: {}".format(args.source))
    start = time.time()
    model = rhino3dm.File3dm.Read(args.source)
    if model is None:
        raise RuntimeError("Could not read source file: {}".format(args.source))
    log("Read complete in {:.1f}s: {} layers, {} objects".format(time.time() - start, len(model.Layers), len(model.Objects)))

    layer_indices = layer_indices_by_name(model, args.layer)
    if not layer_indices:
        raise RuntimeError("Layer not found: {}".format(args.layer))
    source_objects = collect_source_objects(model, set(layer_indices))
    brep_objects = [obj for obj in source_objects if type(obj.geometry).__name__ == "Brep"]
    curve_objects = [obj for obj in source_objects if type(obj.geometry).__name__.endswith("Curve")]

    log("Matched CLT layers: {}".format(layer_indices))
    log("Named CLT objects: {} Breps, {} Curves".format(len(brep_objects), len(curve_objects)))

    all_sheets = []
    side_results = {}
    for side, prefer in (("south", (1.0, 0.0)), ("west", (0.0, 1.0))):
        side_breps = [obj for obj in brep_objects if obj.side == side]
        if not side_breps:
            log("Skipping {}: no Breps".format(side))
            continue
        axis = dominant_edge_axis(side_breps, prefer=prefer)
        log("{}: {} Breps, projection axis ({:.8f}, {:.8f})".format(side.upper(), len(side_breps), axis[0], axis[1]))
        poly, diagnostics = polygons_from_brep_edges(side_breps, axis=axis, samples_per_span=args.samples_per_span)
        if poly.is_empty:
            log("{}: no polygons produced".format(side.upper()))
            continue
        sheets, dims = side_sheets(side, poly)
        all_sheets.extend(sheets)
        width, height, cols, rows = dims
        side_results[side] = {
            "source_breps": len(side_breps),
            "projection_axis_xy": axis,
            "unrolled_size_in": (width, height),
            "tile_grid_cols_rows": (cols, rows),
            "sheets_with_geometry": len(sheets),
            "polygon_geom_type": poly.geom_type,
            "polygon_area_in2": float(poly.area),
            "polygon_stats": geom_stats(poly),
            "diagnostics": diagnostics,
        }
        side_dir = os.path.join(out_dir, "{}_svg_sheets".format(side))
        os.makedirs(side_dir, exist_ok=True)
        for i, sheet in enumerate(sheets, start=1):
            path = os.path.join(side_dir, "{}_clt_sheet_{:02d}_R{}_C{}.svg".format(side, i, sheet["row"], sheet["col"]))
            write_sheet_svg(path, sheet)

    master_path = os.path.join(out_dir, "PROTOTYPE_EXACT_CLT_LASER_MASTER_{}.3dm".format(stamp))
    write_master_3dm(master_path, all_sheets)

    manifest = {
        "source": args.source,
        "output_dir": out_dir,
        "master_3dm": master_path,
        "clt_layer_name": args.layer,
        "matched_layers": layer_inventory(model, layer_indices),
        "model_unit_system": str(model.Settings.ModelUnitSystem),
        "scale": "1 ft real = 0.5 in physical model",
        "score_spacing": "5 ft real = 2.5 in model",
        "stock": "12 x 12 in with 0.125 in margin",
        "method": "Brep edge curves projected to side elevation, polygonized and classified by even/odd nesting per panel, then material unioned by side.",
        "rhino3dm_limitations": "No BrepLoop/BrepTrim access and no Rhino curve boolean/projection helpers; non-polyline NURBS edges are sampled.",
        "rhinocommon_notes": RHINOCOMMON_NOTES,
        "curve_objects_on_duplicate_layers_note": "{} same-name NurbsCurve objects were found, all named SOUTH_CLT_*; the prototype reports them but uses Breps for both SOUTH and WEST.".format(len(curve_objects)),
        "side_results": side_results,
        "total_sheets_with_geometry": len(all_sheets),
    }
    manifest_path = os.path.join(out_dir, "prototype_exact_clt_laser_manifest_{}.json".format(stamp))
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    log("Wrote master: {}".format(master_path))
    log("Wrote manifest: {}".format(manifest_path))
    log("Total sheets with geometry: {}".format(len(all_sheets)))


if __name__ == "__main__":
    main()
