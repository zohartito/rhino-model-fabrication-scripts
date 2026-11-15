#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import rhino3dm
from shapely import affinity


SOURCE_SCRIPT = Path("path/to/prototype_exact_clt_laser.py")
PROJECT_ROOT = Path("path/to/laser-output")
DEFAULT_OUT_ROOT = PROJECT_ROOT / "CLT laser south west FINAL PACKED 9 sheets 12x12"

STOCK_W = 12.0
STOCK_H = 12.0
MARGIN = 0.125
GAP = 0.15
WORK_W = STOCK_W - 2 * MARGIN
WORK_H = STOCK_H - 2 * MARGIN
BED_W = 32.0
BED_H = 18.0
BED_GAP = 2.0


def load_proto():
    spec = importlib.util.spec_from_file_location("proto_clt", SOURCE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {SOURCE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repack CLT laser sheets onto fewer 12x12 boards.")
    parser.add_argument("--source", default=None, help="Source 3dm. Defaults to the prototype source.")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT), help="Output folder.")
    parser.add_argument(
        "--include-unnamed-west-breps",
        action="store_true",
        help="Treat unnamed Breps on the top-level CLT layer as West patch panels.",
    )
    parser.add_argument("--label-suffix", default="", help="Suffix included in output filenames.")
    return parser.parse_args()


@dataclass
class PackedItem:
    source: dict
    label: str
    w: float
    h: float
    piece: object
    scores: list
    x: float = 0.0
    y: float = 0.0
    rotated: bool = False


@dataclass
class Board:
    index: int
    items: list[PackedItem] = field(default_factory=list)
    shelves: list[dict] = field(default_factory=list)


def geom_bounds(piece, scores: Sequence) -> tuple[float, float, float, float]:
    bounds = [piece.bounds]
    for score in scores:
        if not score.is_empty:
            bounds.append(score.bounds)
    minx = min(b[0] for b in bounds)
    miny = min(b[1] for b in bounds)
    maxx = max(b[2] for b in bounds)
    maxy = max(b[3] for b in bounds)
    return minx, miny, maxx, maxy


def normalize_item(sheet: dict) -> PackedItem:
    minx, miny, maxx, maxy = geom_bounds(sheet["piece"], sheet["scores"])
    piece = affinity.translate(sheet["piece"], xoff=-minx, yoff=-miny)
    scores = [affinity.translate(score, xoff=-minx, yoff=-miny) for score in sheet["scores"]]
    label = f'{sheet["side"].upper()} R{sheet["row"]}C{sheet["col"]}'
    return PackedItem(
        source=sheet,
        label=label,
        w=float(maxx - minx),
        h=float(maxy - miny),
        piece=piece,
        scores=scores,
    )


def rotate_item_90(item: PackedItem) -> None:
    item.piece = affinity.rotate(item.piece, 90, origin=(0, 0))
    item.scores = [affinity.rotate(score, 90, origin=(0, 0)) for score in item.scores]
    minx, miny, maxx, maxy = geom_bounds(item.piece, item.scores)
    item.piece = affinity.translate(item.piece, xoff=-minx, yoff=-miny)
    item.scores = [affinity.translate(score, xoff=-minx, yoff=-miny) for score in item.scores]
    item.w = float(maxx - minx)
    item.h = float(maxy - miny)
    item.rotated = True


def orientation_options(item: PackedItem) -> list[tuple[bool, float, float]]:
    options = [(False, item.w, item.h)]
    if abs(item.w - item.h) > 0.001:
        options.append((True, item.h, item.w))
    # Try the orientation with the smaller width first for skinny pieces; this
    # packs leftover strips into the same board instead of wasting another sheet.
    return sorted(options, key=lambda row: (row[1], row[2]))


def pack_items(items: list[PackedItem]) -> list[Board]:
    # First-fit shelf packing with optional 90 degree rotation. Rotating a piece
    # on the plywood is safe here because the cut outline and score lines rotate
    # together; the label still tells where it belongs on the model.
    ordered = sorted(items, key=lambda item: (max(item.w, item.h), item.w * item.h), reverse=True)
    boards: list[Board] = []

    for item in ordered:
        placed = False
        for board in boards:
            # Try existing shelves first.
            for shelf in board.shelves:
                placed_rotation = None
                placed_w = item.w
                placed_h = item.h
                for rotated, trial_w, trial_h in orientation_options(item):
                    if trial_w <= shelf["remaining_w"] + 1e-6 and trial_h <= shelf["height"] + 1e-6:
                        placed_rotation = rotated
                        placed_w = trial_w
                        placed_h = trial_h
                        break
                if placed_rotation is not None:
                    if placed_rotation:
                        rotate_item_90(item)
                    item.x = shelf["x"]
                    item.y = shelf["y"]
                    board.items.append(item)
                    shelf["x"] += placed_w + GAP
                    shelf["remaining_w"] = WORK_W - (shelf["x"] - MARGIN)
                    placed = True
                    break
            if placed:
                break

            # Try a new shelf above the existing shelves.
            used_h = 0.0
            if board.shelves:
                used_h = max(s["y"] + s["height"] - MARGIN for s in board.shelves) + GAP
            placed_rotation = None
            placed_w = item.w
            placed_h = item.h
            for rotated, trial_w, trial_h in orientation_options(item):
                if trial_w <= WORK_W + 1e-6 and used_h + trial_h <= WORK_H + 1e-6:
                    placed_rotation = rotated
                    placed_w = trial_w
                    placed_h = trial_h
                    break
            if placed_rotation is not None:
                if placed_rotation:
                    rotate_item_90(item)
                shelf_y = MARGIN + used_h
                item.x = MARGIN
                item.y = shelf_y
                board.items.append(item)
                board.shelves.append(
                    {
                        "x": MARGIN + placed_w + GAP,
                        "y": shelf_y,
                        "height": placed_h,
                        "remaining_w": WORK_W - placed_w - GAP,
                    }
                )
                placed = True
                break

        if not placed:
            board = Board(index=len(boards) + 1)
            placed_rotation = None
            placed_w = item.w
            placed_h = item.h
            for rotated, trial_w, trial_h in orientation_options(item):
                if trial_w <= WORK_W + 1e-6 and trial_h <= WORK_H + 1e-6:
                    placed_rotation = rotated
                    placed_w = trial_w
                    placed_h = trial_h
                    break
            if placed_rotation:
                rotate_item_90(item)
            item.x = MARGIN
            item.y = MARGIN
            board.items.append(item)
            board.shelves.append(
                {
                    "x": MARGIN + placed_w + GAP,
                    "y": MARGIN,
                    "height": placed_h,
                    "remaining_w": WORK_W - placed_w - GAP,
                }
            )
            boards.append(board)

    return boards


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


def ring_points(coords) -> list[tuple[float, float]]:
    pts = [(float(x), float(y)) for x, y in coords]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def polygon_iter(geom):
    if geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield geom
    elif geom.geom_type == "MultiPolygon":
        yield from geom.geoms
    elif geom.geom_type == "GeometryCollection":
        for part in geom.geoms:
            yield from polygon_iter(part)


def transform_points(points: Sequence[tuple[float, float]], ox: float, oy: float) -> list[tuple[float, float]]:
    return [(x + ox, y + oy) for x, y in points]


def write_packed_3dm(path: Path, boards: list[Board]) -> None:
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Inches
    cut = layer(model, "CUT_RED_FROM_BREP_EDGES", (255, 0, 0, 255))
    score = layer(model, "SCORE_BLUE_5FT_MODULE", (0, 0, 255, 255))
    bed = layer(model, "LASER_BED_32x18_YELLOW", (255, 220, 0, 255))
    stock = layer(model, "MATERIAL_12x12_YELLOW", (255, 220, 0, 255))
    labels = layer(model, "LABELS_BLACK_DO_NOT_CUT", (0, 0, 0, 255))
    piece_bbox = layer(model, "PIECE_BOUNDS_MAGENTA_DO_NOT_CUT", (255, 0, 255, 255))

    drawn_beds = set()
    for i, board in enumerate(boards):
        bed_index = i // 2
        slot = i % 2
        bed_x = 0.0
        bed_y = bed_index * (BED_H + BED_GAP)
        stock_x = bed_x + 0.5 + slot * 12.75
        stock_y = bed_y + 0.5
        if bed_index not in drawn_beds:
            add_rect(model, bed_x, bed_y, BED_W, BED_H, bed, f"laser_bed_{bed_index + 1:02d}")
            drawn_beds.add(bed_index)
        add_rect(model, stock_x, stock_y, STOCK_W, STOCK_H, stock, f"BOARD_{board.index:02d}_12x12")
        add_label(model, f"BOARD {board.index:02d}", stock_x + 0.2, stock_y + 0.25, labels)

        for item in board.items:
            ox = stock_x + item.x
            oy = stock_y + item.y
            add_rect(model, ox, oy, item.w, item.h, piece_bbox, f"{item.label}_bounds")
            add_label(model, item.label, ox + 0.05, oy + 0.10, labels)
            for poly in polygon_iter(item.piece):
                add_polyline(model, transform_points(ring_points(poly.exterior.coords), ox, oy), cut, f"{item.label}_cut_outer")
                for interior in poly.interiors:
                    add_polyline(model, transform_points(ring_points(interior.coords), ox, oy), cut, f"{item.label}_cut_hole")
            for seg in item.scores:
                pts = [(float(x), float(y)) for x, y in seg.coords]
                add_polyline(model, transform_points(pts, ox, oy), score, f"{item.label}_score")

    model.Write(str(path), 8)


def main() -> None:
    args = parse_args()
    proto = load_proto()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    source = args.source or proto.SOURCE
    model = rhino3dm.File3dm.Read(source)
    layer_indices = proto.layer_indices_by_name(model, proto.CLT_LAYER)
    source_objects = proto.collect_source_objects(model, set(layer_indices))
    brep_objects = [obj for obj in source_objects if type(obj.geometry).__name__ == "Brep"]
    unnamed_west_patch_indices = []
    if args.include_unnamed_west_breps:
        top_level_indices = [
            i
            for i in layer_indices
            if model.Layers[i].FullPath == proto.CLT_LAYER
        ]
        for obj_index, obj in enumerate(model.Objects):
            if obj.Attributes.LayerIndex not in top_level_indices:
                continue
            name = obj.Attributes.Name or ""
            if name.startswith(("SOUTH_CLT_", "WEST_CLT_")):
                continue
            if type(obj.Geometry).__name__ != "Brep":
                continue
            brep_objects.append(proto.SourceObject(obj_index, name or f"UNNAMED_WEST_PATCH_{len(unnamed_west_patch_indices)+1:02d}", "west", obj.Geometry))
            unnamed_west_patch_indices.append(obj_index)

    all_sheets = []
    side_results = {}
    for side, prefer in (("south", (1.0, 0.0)), ("west", (0.0, 1.0))):
        side_breps = [obj for obj in brep_objects if obj.side == side]
        axis = proto.dominant_edge_axis(side_breps, prefer=prefer)
        poly, diagnostics = proto.polygons_from_brep_edges(side_breps, axis=axis, samples_per_span=16)
        sheets, dims = proto.side_sheets(side, poly)
        all_sheets.extend(sheets)
        side_results[side] = {
            "source_breps": len(side_breps),
            "projection_axis_xy": axis,
            "unrolled_size_in": dims[:2],
            "original_grid_cols_rows": dims[2:],
            "original_grid_sheets": len(sheets),
            "diagnostics": diagnostics,
        }

    items = [normalize_item(sheet) for sheet in all_sheets]
    boards = pack_items(items)
    suffix = f"_{args.label_suffix}" if args.label_suffix else ""
    out_3dm = out_root / f"CLT_LASER_SOUTH_WEST_PACKED{suffix}_{len(boards)}SHEETS_{stamp}.3dm"
    write_packed_3dm(out_3dm, boards)

    manifest = {
        "source": source,
        "source_script": str(SOURCE_SCRIPT),
        "output_3dm": str(out_3dm),
        "included_unnamed_west_patch_brep_indices": unnamed_west_patch_indices,
        "scale": "1 ft real = 0.5 in physical model",
        "stock": "12 x 12 in, packed with 0.125 in outside margin and 0.15 in gap between pieces",
        "important": "This packed file keeps every original partial tile cut; it only moves partial pieces onto fewer 12x12 boards.",
        "original_unpacked_sheets": len(all_sheets),
        "packed_boards_needed": len(boards),
        "side_results": side_results,
        "boards": [
            {
                "board": board.index,
                "items": [
                    {
                        "label": item.label,
                        "size_in": [round(item.w, 4), round(item.h, 4)],
                        "placed_at_in": [round(item.x, 4), round(item.y, 4)],
                        "rotated_90_on_sheet": item.rotated,
                    }
                    for item in board.items
                ],
            }
            for board in boards
        ],
    }
    manifest_path = out_root / f"CLT_LASER_SOUTH_WEST_PACKED{suffix}_{len(boards)}SHEETS_manifest_{stamp}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    guide = out_root / f"PACKED_{len(boards)}_SHEET_CUT_GUIDE_READ_ME.md"
    lines = [
        "# CLT Packed 12x12 Cut Guide",
        "",
        f"Use Rhino file: `{out_3dm}`",
        "",
        f"Real plywood sheets needed: `{len(boards)}` plus at least one spare.",
        "",
        "This version keeps all partial cuts from the original 15-sheet layout, but packs small edge/top pieces onto shared 12x12 boards.",
        "",
        "Layers:",
        "",
        "- Red: cut",
        "- Blue: score",
        "- Yellow: 12x12 material and 32x18 bed guides, do not cut",
        "- Black: labels, do not cut",
        "- Magenta: piece bounding boxes, do not cut",
        "",
        "Board contents:",
        "",
    ]
    for board in boards:
        labels = ", ".join(item.label + (" (rotated)" if item.rotated else "") for item in board.items)
        lines.append(f"- Board {board.index:02d}: {labels}")
    lines.extend(
        [
            "",
            "Assembly orientation reminder:",
            "",
            "- `R1` is bottom row.",
            "- `C1` is the side closest to the South/West corner.",
            "- For the West outside view, read columns mirrored: `C3 C2 C1` left-to-right.",
        ]
    )
    guide.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(out_3dm)
    print(manifest_path)
    print(guide)
    print(json.dumps(manifest["boards"], indent=2))


if __name__ == "__main__":
    main()
