import math
import os
import time
from xml.sax.saxutils import escape

import rhino3dm
from shapely import affinity
from shapely.geometry import box, LineString
from shapely.ops import polygonize, unary_union


SOURCE = "path/to/your-model.3dm"
OUT_ROOT = "path/to/laser-output"
OUT_DIR = os.path.join(OUT_ROOT, "CLT laser south west EXACT local axis 12x12")
CLT_LAYER = "20_CLT_THICK_REMAP_49FT_BACKUP_WALL_V68"

SCALE_IN_PER_FT = 0.5
SCORE_SPACING_IN = 2.5

STOCK_W_IN = 12.0
STOCK_H_IN = 12.0
STOCK_MARGIN_IN = 0.125
WORK_W_IN = STOCK_W_IN - 2 * STOCK_MARGIN_IN
WORK_H_IN = STOCK_H_IN - 2 * STOCK_MARGIN_IN

BED_W_IN = 32.0
BED_H_IN = 18.0
BED_GAP_IN = 2.0
STAMP = time.strftime("%Y%m%d_%H%M%S")


def layer_index(model, name):
    for i, layer in enumerate(model.Layers):
        if layer.Name == name:
            return i
    raise RuntimeError("Layer not found: {}".format(name))


def curve_points(curve):
    # CLT panel Brep edges are mostly straight. Sample NURBS edges defensively.
    if curve.IsLinear():
        return [curve.PointAtStart, curve.PointAtEnd]
    domain = curve.Domain
    t0 = domain.T0
    t1 = domain.T1
    pts = []
    steps = max(8, min(64, int(curve.GetBoundingBox().Diagonal.Length / 0.25) if hasattr(curve.GetBoundingBox(), "Diagonal") else 16))
    for i in range(steps + 1):
        t = t0 + (t1 - t0) * i / steps
        pts.append(curve.PointAt(t))
    return pts


def project_point(pt, axis):
    return ((pt.X * axis[0] + pt.Y * axis[1]) * SCALE_IN_PER_FT, pt.Z * SCALE_IN_PER_FT)


def projected_brep_polygon(brep, axis):
    # These CLT objects are simple solid fragments. Projecting their actual
    # vertices onto the wall-local elevation plane and taking the hull avoids
    # the earlier bounding-box inflation while staying robust for tiny slivers.
    pts = [project_point(vertex.Location, axis) for vertex in brep.Vertices]
    if len(pts) < 3:
        return None
    hull = unary_union([LineString([pts[i], pts[(i + 1) % len(pts)]]) for i in range(len(pts))]).convex_hull
    return hull if hull.area > 1e-8 else None


def selected_breps(model, prefix):
    li = layer_index(model, CLT_LAYER)
    result = []
    for obj in model.Objects:
        if obj.Attributes.LayerIndex != li:
            continue
        if type(obj.Geometry).__name__ != "Brep":
            continue
        name = obj.Attributes.Name or ""
        if name.startswith(prefix):
            result.append((name, obj.Geometry))
    return result


def side_geometry(items, axis):
    polys = []
    dropped = []
    for name, brep in items:
        poly = projected_brep_polygon(brep, axis)
        if poly is None or poly.is_empty or poly.area <= 1e-8:
            dropped.append(name)
            continue
        polys.append(poly)
    raw = unary_union(polys)
    # Heal tiny panel-contact slivers without erasing real window openings.
    healed = raw.buffer(0.002).buffer(-0.002)
    minx, miny, _, _ = healed.bounds
    normalized = affinity.translate(healed, xoff=-minx, yoff=-miny)
    return normalized, dropped


def polygon_iter(geom):
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


def line_iter(geom):
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


def ring_points(coords):
    pts = [(float(x), float(y)) for x, y in coords]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def side_sheets(side, poly):
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
            if piece.is_empty or piece.area < 0.002:
                continue
            piece = affinity.translate(piece, xoff=-x0 + STOCK_MARGIN_IN, yoff=-y0 + STOCK_MARGIN_IN)
            score_segments = []
            sx = SCORE_SPACING_IN
            while sx < width - 1e-6:
                line = LineString([(sx, -1.0), (sx, height + 1.0)])
                clipped = line.intersection(poly).intersection(tile)
                for seg in line_iter(clipped):
                    if seg.length > 0.05:
                        score_segments.append(affinity.translate(seg, xoff=-x0 + STOCK_MARGIN_IN, yoff=-y0 + STOCK_MARGIN_IN))
                sx += SCORE_SPACING_IN
            sheets.append({"side": side, "row": row + 1, "col": col + 1, "rows": rows, "cols": cols, "piece": piece, "scores": score_segments})
    return sheets, (width, height, cols, rows)


def add_layer(model, name, color):
    layer = rhino3dm.Layer()
    layer.Name = name
    layer.Color = color
    return model.Layers.Add(layer)


def attrs(layer_id, name=None):
    a = rhino3dm.ObjectAttributes()
    a.LayerIndex = layer_id
    if name:
        a.Name = name
    return a


def add_polyline(model, points, layer_id, name=None, ox=0, oy=0):
    if len(points) < 2:
        return
    pl = rhino3dm.Polyline()
    for x, y in points:
        pl.Add(float(x + ox), float(y + oy), 0.0)
    model.Objects.AddPolyline(pl, attrs(layer_id, name))


def add_rect(model, x, y, w, h, layer_id, name=None):
    add_polyline(model, [(0, 0), (w, 0), (w, h), (0, h), (0, 0)], layer_id, name, x, y)


def add_label(model, text, x, y, layer_id):
    model.Objects.AddTextDot(text, rhino3dm.Point3d(float(x), float(y), 0.0), attrs(layer_id, text))


def svg_path(points):
    if not points:
        return ""
    parts = ["M {:.4f} {:.4f}".format(points[0][0], STOCK_H_IN - points[0][1])]
    for x, y in points[1:]:
        parts.append("L {:.4f} {:.4f}".format(x, STOCK_H_IN - y))
    return " ".join(parts)


def write_svg(path, sheet):
    with open(path, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<svg xmlns="http://www.w3.org/2000/svg" width="12in" height="12in" viewBox="0 0 12 12">\n')
        f.write('<rect x="0" y="0" width="12" height="12" fill="none" stroke="#ffcc00" stroke-width="0.003"/>\n')
        f.write('<text x="0.18" y="0.35" font-size="0.18" fill="black">{}</text>\n'.format(escape("{} R{}C{}".format(sheet["side"].upper(), sheet["row"], sheet["col"]))))
        for poly in polygon_iter(sheet["piece"]):
            f.write('<path d="{}" fill="none" stroke="red" stroke-width="0.003"/>\n'.format(svg_path(ring_points(poly.exterior.coords))))
            for interior in poly.interiors:
                f.write('<path d="{}" fill="none" stroke="red" stroke-width="0.003"/>\n'.format(svg_path(ring_points(interior.coords))))
        for score in sheet["scores"]:
            f.write('<path d="{}" fill="none" stroke="blue" stroke-width="0.003"/>\n'.format(svg_path([(float(x), float(y)) for x, y in score.coords])))
        f.write("</svg>\n")


def write_master(path, sheets):
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Inches
    cut = add_layer(model, "CUT_RED_EXACT_CLT_EDGES", (255, 0, 0, 255))
    score = add_layer(model, "SCORE_BLUE_5FT_MODULE", (0, 0, 255, 255))
    bed = add_layer(model, "LASER_BED_32x18_YELLOW", (255, 220, 0, 255))
    stock = add_layer(model, "MATERIAL_12x12_YELLOW", (255, 220, 0, 255))
    labels = add_layer(model, "LABELS_BLACK_DO_NOT_CUT", (0, 0, 0, 255))

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
        add_label(model, "{} exact sheet {} R{} C{}".format(sheet["side"].upper(), i + 1, sheet["row"], sheet["col"]), stock_x + 0.2, stock_y + 0.25, labels)
        for poly in polygon_iter(sheet["piece"]):
            add_polyline(model, ring_points(poly.exterior.coords), cut, "cut_outer_exact", stock_x, stock_y)
            for interior in poly.interiors:
                add_polyline(model, ring_points(interior.coords), cut, "cut_window_or_hole_exact", stock_x, stock_y)
        for seg in sheet["scores"]:
            add_polyline(model, [(float(x), float(y)) for x, y in seg.coords], score, "score_5ft", stock_x, stock_y)
    model.Write(path, 8)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    model = rhino3dm.File3dm.Read(SOURCE)
    jobs = [
        ("south", "SOUTH_CLT", (0.97620, -0.21687)),
        ("west", "WEST_CLT", (-0.21694, 0.97618)),
    ]
    all_sheets = []
    manifest = [
        "Source: {}".format(SOURCE),
        "CLT layer: {}".format(CLT_LAYER),
        "Model units: {}".format(model.Settings.ModelUnitSystem),
        "Scale: 1 ft real = 0.5 in physical model",
        "Score spacing: 5 ft real = 2.5 in model",
        "Extraction: exact projected Brep vertices from named SOUTH_CLT / WEST_CLT objects onto wall-local axes, not bounding boxes",
        "Stock: 12 x 12 x 1/8 in Baltic birch",
        "",
    ]
    for side, prefix, axis in jobs:
        items = selected_breps(model, prefix)
        geom, dropped = side_geometry(items, axis)
        sheets, dims = side_sheets(side, geom)
        all_sheets.extend(sheets)
        side_dir = os.path.join(OUT_DIR, "{}_svg_sheets".format(side))
        os.makedirs(side_dir, exist_ok=True)
        for i, sheet in enumerate(sheets, start=1):
            write_svg(os.path.join(side_dir, "{}_exact_clt_sheet_{:02d}_R{}_C{}.svg".format(side, i, sheet["row"], sheet["col"])), sheet)
        width, height, cols, rows = dims
        holes = sum(len(poly.interiors) for poly in polygon_iter(geom))
        manifest.extend([
            "{}:".format(side.upper()),
            "  object prefix: {}".format(prefix),
            "  source Breps: {}".format(len(items)),
            "  dropped Breps: {}".format(len(dropped)),
            "  exact projected size: {:.3f} in x {:.3f} in".format(width, height),
            "  tile grid: {} columns x {} rows".format(cols, rows),
            "  plywood sheets with geometry: {}".format(len(sheets)),
        "  detected holes/interior loops after union: {}".format(holes),
        "  wall-local axis: ({:.5f}, {:.5f})".format(axis[0], axis[1]),
            "",
        ])
    master = os.path.join(OUT_DIR, "CLT_SOUTH_WEST_EXACT_LOCALAXIS_12x12_LASER_MASTER_{}.3dm".format(STAMP))
    write_master(master, all_sheets)
    manifest.extend([
        "TOTAL plywood sheets with geometry: {}".format(len(all_sheets)),
        "Master Rhino laser file: {}".format(master),
        "WARNING: Prior non-EXACT folder used bounding-box projection and should not be used for final window punch geometry.",
    ])
    manifest_path = os.path.join(OUT_DIR, "CLT_SOUTH_WEST_EXACT_LOCALAXIS_12x12_MANIFEST_{}.txt".format(STAMP))
    with open(manifest_path, "w") as f:
        f.write("\n".join(manifest) + "\n")
    print(master)
    print(manifest_path)
    print("\n".join(manifest))


if __name__ == "__main__":
    main()
