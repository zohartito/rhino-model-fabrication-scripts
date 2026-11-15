import math
import os
import time
from xml.sax.saxutils import escape

import rhino3dm
from shapely import affinity
from shapely.geometry import box, LineString, MultiLineString, GeometryCollection
from shapely.ops import unary_union


SOURCE = "path/to/your-model.3dm"
OUT_ROOT = "path/to/laser-output"
OUT_DIR = os.path.join(OUT_ROOT, "CLT laser south west 12x12")
CLT_LAYER = "20_CLT_THICK_REMAP_49FT_BACKUP_WALL_V68"

# Physical model scale: 1 real foot = 1/2 model inch.
SCALE_IN_PER_FT = 0.5

# Teacher's CLT factory-panel module: 5 real ft = 2.5 model inches.
SCORE_SPACING_IN = 2.5

# Purchased material. A small inset keeps cut vectors off the exact plywood edge.
STOCK_W_IN = 12.0
STOCK_H_IN = 12.0
STOCK_MARGIN_IN = 0.125
WORK_W_IN = STOCK_W_IN - 2 * STOCK_MARGIN_IN
WORK_H_IN = STOCK_H_IN - 2 * STOCK_MARGIN_IN

# A common architecture-lab Universal bed setup. Stock size is still the actual limit.
BED_W_IN = 32.0
BED_H_IN = 18.0
BED_GAP_IN = 2.0

STAMP = time.strftime("%Y%m%d_%H%M%S")


def layer_index(model, name):
    for i, layer in enumerate(model.Layers):
        if layer.Name == name:
            return i
    raise RuntimeError("Layer not found: {}".format(name))


def brep_rows(model):
    target = layer_index(model, CLT_LAYER)
    rows = []
    for index, obj in enumerate(model.Objects):
        if obj.Attributes.LayerIndex != target:
            continue
        if type(obj.Geometry).__name__ != "Brep":
            continue
        bb = obj.Geometry.GetBoundingBox()
        dx = bb.Max.X - bb.Min.X
        dy = bb.Max.Y - bb.Min.Y
        dz = bb.Max.Z - bb.Min.Z
        if dz <= 0.01:
            continue
        rows.append(
            {
                "index": index,
                "bbox": (bb.Min.X, bb.Min.Y, bb.Min.Z, bb.Max.X, bb.Max.Y, bb.Max.Z),
                "center": (
                    0.5 * (bb.Min.X + bb.Max.X),
                    0.5 * (bb.Min.Y + bb.Max.Y),
                    0.5 * (bb.Min.Z + bb.Max.Z),
                ),
                "dims": (dx, dy, dz),
            }
        )
    return rows


def select_south(rows):
    # Use the full L-shaped CLT wall projected to X/Z.
    # This keeps the corner bay that otherwise gets missed by a strict south-leg selector.
    return list(rows)


def select_west(rows):
    # Use the full L-shaped CLT wall projected to Y/Z.
    # This mirrors the south projection logic and keeps the corner bay.
    return list(rows)


def pca_axis(rows, prefer):
    pts = [(row["center"][0], row["center"][1]) for row in rows]
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    cxx = sum((p[0] - mx) ** 2 for p in pts) / len(pts)
    cyy = sum((p[1] - my) ** 2 for p in pts) / len(pts)
    cxy = sum((p[0] - mx) * (p[1] - my) for p in pts) / len(pts)
    angle = 0.5 * math.atan2(2 * cxy, cxx - cyy)
    axis = (math.cos(angle), math.sin(angle))
    if prefer == "x" and axis[0] < 0:
        axis = (-axis[0], -axis[1])
    if prefer == "y" and axis[1] < 0:
        axis = (-axis[0], -axis[1])
    return axis


def project_point_xy(x, y, axis):
    return x * axis[0] + y * axis[1]


def row_projected_rect(row, axis):
    xmin, ymin, zmin, xmax, ymax, zmax = row["bbox"]
    s_vals = [
        project_point_xy(x, y, axis)
        for x in (xmin, xmax)
        for y in (ymin, ymax)
    ]
    return min(s_vals), zmin, max(s_vals), zmax


def side_polygon(rows, axis):
    rects = []
    for row in rows:
        s0, z0, s1, z1 = row_projected_rect(row, axis)
        rects.append(box(s0 * SCALE_IN_PER_FT, z0 * SCALE_IN_PER_FT, s1 * SCALE_IN_PER_FT, z1 * SCALE_IN_PER_FT))
    merged = unary_union(rects)
    # Close hairline modeling gaps without filling real windows.
    merged = merged.buffer(0.01).buffer(-0.01)
    minx, miny, _, _ = merged.bounds
    merged = affinity.translate(merged, xoff=-minx, yoff=-miny)
    return merged


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


def layer(model, name, color):
    item = rhino3dm.Layer()
    item.Name = name
    item.Color = color
    return model.Layers.Add(item)


def attrs(layer_id, name=None):
    a = rhino3dm.ObjectAttributes()
    a.LayerIndex = layer_id
    if name:
        a.Name = name
    return a


def add_polyline(model, points, layer_id, name=None):
    if len(points) < 2:
        return
    pl = rhino3dm.Polyline()
    for x, y in points:
        pl.Add(float(x), float(y), 0.0)
    model.Objects.AddPolyline(pl, attrs(layer_id, name))


def add_rect(model, x, y, w, h, layer_id, name=None):
    add_polyline(model, [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], layer_id, name)


def add_label(model, text, x, y, layer_id):
    model.Objects.AddTextDot(text, rhino3dm.Point3d(float(x), float(y), 0.0), attrs(layer_id, text))


def transform_points(points, ox, oy):
    return [(x + ox, y + oy) for x, y in points]


def side_sheets(name, poly):
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
            # Translate local piece into 12x12 stock coordinates with margin.
            piece_local = affinity.translate(piece, xoff=-x0 + STOCK_MARGIN_IN, yoff=-y0 + STOCK_MARGIN_IN)

            score_segments = []
            score_x = SCORE_SPACING_IN
            while score_x < width - 0.001:
                line = LineString([(score_x, -1.0), (score_x, height + 1.0)])
                seg = line.intersection(poly).intersection(tile)
                for ln in line_iter(seg):
                    if ln.length > 0.05:
                        score_segments.append(affinity.translate(ln, xoff=-x0 + STOCK_MARGIN_IN, yoff=-y0 + STOCK_MARGIN_IN))
                score_x += SCORE_SPACING_IN

            sheets.append(
                {
                    "side": name,
                    "row": row + 1,
                    "col": col + 1,
                    "cols": cols,
                    "rows": rows,
                    "piece": piece_local,
                    "scores": score_segments,
                }
            )
    return sheets, (width, height, cols, rows)


def svg_path(points):
    if not points:
        return ""
    parts = ["M {:.4f} {:.4f}".format(points[0][0], STOCK_H_IN - points[0][1])]
    for x, y in points[1:]:
        parts.append("L {:.4f} {:.4f}".format(x, STOCK_H_IN - y))
    return " ".join(parts)


def write_sheet_svg(path, sheet):
    paths = []
    for poly in polygon_iter(sheet["piece"]):
        paths.append(("red", ring_points(poly.exterior.coords)))
        for interior in poly.interiors:
            paths.append(("red", ring_points(interior.coords)))
    for score in sheet["scores"]:
        paths.append(("blue", [(float(x), float(y)) for x, y in score.coords]))

    label = "{} R{}C{}".format(sheet["side"].upper(), sheet["row"], sheet["col"])
    with open(path, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<svg xmlns="http://www.w3.org/2000/svg" width="12in" height="12in" viewBox="0 0 12 12">\n')
        f.write('<rect x="0" y="0" width="12" height="12" fill="none" stroke="#ffcc00" stroke-width="0.003"/>\n')
        f.write('<text x="0.18" y="0.35" font-size="0.18" fill="black">{}</text>\n'.format(escape(label)))
        for color, pts in paths:
            f.write('<path d="{}" fill="none" stroke="{}" stroke-width="0.003"/>\n'.format(svg_path(pts), color))
        f.write('</svg>\n')


def write_master_3dm(path, sheets):
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Inches
    cut = layer(model, "CUT_RED", (255, 0, 0, 255))
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


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    model = rhino3dm.File3dm.Read(SOURCE)
    rows = brep_rows(model)
    sides = [
        ("south", select_south(rows), (1.0, 0.0)),
        ("west", select_west(rows), (0.0, 1.0)),
    ]

    all_sheets = []
    manifest = []
    manifest.append("Source: {}".format(SOURCE))
    manifest.append("CLT layer: {}".format(CLT_LAYER))
    manifest.append("Model units: {}".format(model.Settings.ModelUnitSystem))
    manifest.append("Scale: 1 ft real = 0.5 in physical model")
    manifest.append("Score spacing: 5 ft real = 2.5 in model")
    manifest.append("Stock: 12 x 12 x 1/8 in Baltic birch")
    manifest.append("Work area per sheet: {:.3f} x {:.3f} in with {:.3f} in margin".format(WORK_W_IN, WORK_H_IN, STOCK_MARGIN_IN))
    manifest.append("Note: wall height is about 24.77 in, so 12 in stock forces a skinny third-row cap strip unless the scale is reduced or larger stock is used.")
    manifest.append("")

    for side_name, side_rows, axis in sides:
        poly = side_polygon(side_rows, axis)
        sheets, dims = side_sheets(side_name, poly)
        all_sheets.extend(sheets)
        width, height, cols, rows_count = dims
        manifest.append("{}:".format(side_name.upper()))
        manifest.append("  source Breps: {}".format(len(side_rows)))
        manifest.append("  unrolled size: {:.3f} in x {:.3f} in".format(width, height))
        manifest.append("  tile grid: {} columns x {} rows".format(cols, rows_count))
        manifest.append("  plywood sheets with geometry: {}".format(len(sheets)))
        manifest.append("  PCA axis in plan: ({:.6f}, {:.6f})".format(axis[0], axis[1]))
        manifest.append("")

        side_dir = os.path.join(OUT_DIR, "{}_svg_sheets".format(side_name))
        os.makedirs(side_dir, exist_ok=True)
        for i, sheet in enumerate(sheets, start=1):
            svg_path_out = os.path.join(side_dir, "{}_clt_sheet_{:02d}_R{}_C{}.svg".format(side_name, i, sheet["row"], sheet["col"]))
            write_sheet_svg(svg_path_out, sheet)

    master_path = os.path.join(OUT_DIR, "CLT_SOUTH_WEST_12x12_LASER_MASTER_{}.3dm".format(STAMP))
    write_master_3dm(master_path, all_sheets)

    manifest.append("TOTAL plywood sheets with geometry: {}".format(len(all_sheets)))
    manifest.append("Master Rhino laser file: {}".format(master_path))
    manifest.append("Layer colors:")
    manifest.append("  CUT_RED = red cut vectors")
    manifest.append("  SCORE_BLUE_5FT_MODULE = blue score vectors every 5 real ft / 2.5 model in")
    manifest.append("  LASER_BED_32x18_YELLOW = yellow 32x18 bed guide")
    manifest.append("  MATERIAL_12x12_YELLOW = yellow plywood stock guide")
    manifest.append("  LABELS_BLACK_DO_NOT_CUT = sheet labels")
    manifest_path = os.path.join(OUT_DIR, "CLT_SOUTH_WEST_12x12_MANIFEST_{}.txt".format(STAMP))
    with open(manifest_path, "w") as f:
        f.write("\n".join(manifest) + "\n")
    print(master_path)
    print(manifest_path)
    print("\n".join(manifest))


if __name__ == "__main__":
    main()
