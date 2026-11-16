#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import rhino3dm


SOURCE = Path("path/to/your-model.3dm")
OUT_DIR = Path("path/to/west-return-bands")
RETURN_LAYER = "16_CU_PUNCH_RETURNS_WEST_V61"

# Source model is in real feet. Printing/model files use 1 real ft = 1/2 in = 12.7 mm.
MM_PER_SOURCE_FT = 12.7

SIDE_ORDER = ["sill", "head", "jamb_S", "jamb_N"]


def safe_token(value: float) -> str:
    return f"{value:.3f}".replace(".", "p").replace("-", "m")


def find_layer(model: rhino3dm.File3dm, layer_name: str) -> int:
    matches = [
        i
        for i, layer in enumerate(model.Layers)
        if layer.Name == layer_name or layer.FullPath == layer_name or layer.FullPath.endswith(layer_name)
    ]
    if not matches:
        raise RuntimeError(f"Missing return layer: {layer_name}")
    # Use the top-level modeled layer, not visible/clipping curve copies.
    for i in matches:
        if model.Layers[i].FullPath == layer_name:
            return i
    return matches[0]


def add_layer(file3dm: rhino3dm.File3dm, name: str, color: tuple[int, int, int, int]) -> int:
    layer = rhino3dm.Layer()
    layer.Name = name
    layer.Color = color
    return file3dm.Layers.Add(layer)


def attrs(layer_index: int, name: str) -> rhino3dm.ObjectAttributes:
    attr = rhino3dm.ObjectAttributes()
    attr.LayerIndex = layer_index
    attr.Name = name
    return attr


def scale_transform() -> rhino3dm.Transform:
    return rhino3dm.Transform.Scale(rhino3dm.Point3d(0, 0, 0), MM_PER_SOURCE_FT)


def brep_bbox_tuple(brep: rhino3dm.Brep) -> tuple[float, float, float, float, float, float]:
    bb = brep.GetBoundingBox()
    return (bb.Min.X, bb.Min.Y, bb.Min.Z, bb.Max.X, bb.Max.Y, bb.Max.Z)


def parse_return_name(name: str) -> tuple[int, str] | None:
    match = re.search(r"WEST_.*?_(\d{3})_(sill|head|jamb_S|jamb_N)$", name)
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def ordinal_label(index: int, total: int) -> str:
    if index == 1:
        return "bottom"
    if index == total:
        return "top"
    names = {
        2: "2nd_from_bottom",
        3: "3rd_from_bottom",
        4: "4th_from_bottom",
        5: "5th_from_bottom",
        6: "6th_from_bottom",
        7: "7th_from_bottom",
        8: "8th_from_bottom",
        9: "9th_from_bottom",
    }
    return names.get(index, f"band_{index:02d}")


def collect_returns(model: rhino3dm.File3dm, layer_index: int):
    returns: dict[int, dict[str, dict]] = defaultdict(dict)
    unmatched = []
    for object_index, obj in enumerate(model.Objects):
        if obj.Attributes.LayerIndex != layer_index:
            continue
        if type(obj.Geometry).__name__ != "Brep":
            unmatched.append({"object_index": object_index, "name": obj.Attributes.Name or "", "type": type(obj.Geometry).__name__})
            continue
        name = obj.Attributes.Name or ""
        parsed = parse_return_name(name)
        if parsed is None:
            unmatched.append({"object_index": object_index, "name": name, "type": type(obj.Geometry).__name__})
            continue
        return_id, side = parsed
        if side in returns[return_id]:
            previous = returns[return_id][side]
            raise RuntimeError(
                f"Duplicate West return {return_id:03d} side {side}: "
                f"object {previous['object_index']} ({previous['name']!r}) and "
                f"object {object_index} ({name!r})"
            )
        returns[return_id][side] = {
            "object_index": object_index,
            "name": name,
            "geometry": obj.Geometry,
            "bbox": brep_bbox_tuple(obj.Geometry),
        }
    return returns, unmatched


def opening_bounds(items: dict[str, dict]) -> tuple[float, float, float, float, float, float]:
    bbs = [item["bbox"] for item in items.values()]
    return tuple(
        [
            min(bb[0] for bb in bbs),
            min(bb[1] for bb in bbs),
            min(bb[2] for bb in bbs),
            max(bb[3] for bb in bbs),
            max(bb[4] for bb in bbs),
            max(bb[5] for bb in bbs),
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Reading source: {SOURCE}")
    model = rhino3dm.File3dm.Read(str(SOURCE))
    if model is None:
        raise RuntimeError(f"Could not read {SOURCE}")
    layer_index = find_layer(model, RETURN_LAYER)
    returns, unmatched = collect_returns(model, layer_index)

    bad = []
    complete = []
    for return_id in sorted(returns):
        sides = returns[return_id]
        if sorted(sides) != sorted(SIDE_ORDER):
            bad.append({"return_id": return_id, "sides": sorted(sides)})
            continue
        bbox = opening_bounds(sides)
        complete.append({"return_id": return_id, "sides": sides, "bbox": bbox})

    if bad:
        raise RuntimeError(f"Found incomplete West returns: {bad[:10]}")

    by_z0: dict[float, list[dict]] = defaultdict(list)
    for item in complete:
        z0 = round(item["bbox"][2], 3)
        by_z0[z0].append(item)

    manifest = {
        "source": str(SOURCE),
        "source_units": str(model.Settings.ModelUnitSystem),
        "source_layer": RETURN_LAYER,
        "output_units": "Millimeters",
        "scale": "1 source foot = 12.7 mm physical model scale",
        "grouping": "West returns grouped by opening bottom elevation z0, bottom-to-top. Object order in each output is return id, then sill/head/jamb_S/jamb_N.",
        "total_source_return_surfaces": sum(len(v) for v in returns.values()),
        "total_complete_returns": len(complete),
        "unmatched_objects": unmatched,
        "incomplete_returns": bad,
        "bands": [],
    }

    xform = scale_transform()
    total_bands = len(by_z0)
    for band_index, z0 in enumerate(sorted(by_z0), start=1):
        rows = sorted(by_z0[z0], key=lambda row: (row["bbox"][1], row["return_id"]))
        label = ordinal_label(band_index, total_bands)
        z1_min = min(row["bbox"][5] for row in rows)
        z1_max = max(row["bbox"][5] for row in rows)
        y0 = min(row["bbox"][1] for row in rows)
        y1 = max(row["bbox"][4] for row in rows)
        file_name = (
            f"west_return_band_{band_index:02d}_{label}_"
            f"z{safe_token(z0)}ft_ids{rows[0]['return_id']:03d}-{rows[-1]['return_id']:03d}_SOURCE_SURFACES_mm.3dm"
        )
        out_path = OUT_DIR / file_name

        out = rhino3dm.File3dm()
        out.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Millimeters
        out.Settings.ModelAbsoluteTolerance = 0.01
        layer_id = add_layer(out, f"WEST_RETURN_BAND_{band_index:02d}_{label}_SOURCE_SURFACES", (166, 89, 28, 255))

        surface_count = 0
        for row in rows:
            for side in SIDE_ORDER:
                source = row["sides"][side]
                brep = source["geometry"].Duplicate()
                brep.Transform(xform)
                name = f"WEST_RETURN_BAND_{band_index:02d}_RID{row['return_id']:03d}_{side}"
                out.Objects.AddBrep(brep, attrs(layer_id, name))
                surface_count += 1

        ok = out.Write(str(out_path), 8)
        if not ok:
            raise RuntimeError(f"Failed to write {out_path}")

        manifest["bands"].append(
            {
                "band": band_index,
                "label": label,
                "file": str(out_path),
                "return_count": len(rows),
                "surface_count": surface_count,
                "return_ids": [row["return_id"] for row in rows],
                "z0_ft": z0,
                "z1_min_ft": round(z1_min, 3),
                "z1_max_ft": round(z1_max, 3),
                "z0_mm": round(z0 * MM_PER_SOURCE_FT, 3),
                "z1_min_mm": round(z1_min * MM_PER_SOURCE_FT, 3),
                "z1_max_mm": round(z1_max * MM_PER_SOURCE_FT, 3),
                "y_span_ft": [round(y0, 3), round(y1, 3)],
                "y_span_mm": [round(y0 * MM_PER_SOURCE_FT, 3), round(y1 * MM_PER_SOURCE_FT, 3)],
            }
        )
        print(f"Band {band_index:02d}: {label}, returns={len(rows)}, surfaces={surface_count}, wrote {out_path.name}")

    manifest_path = OUT_DIR / "west_returns_split_by_band_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    readme_lines = [
        "# West Returns Split By Band",
        "",
        f"Source: `{SOURCE}`",
        "",
        f"Source layer: `{RETURN_LAYER}`",
        "",
        "These are source return surfaces, separated by horizontal band and scaled to physical model millimeters.",
        "",
        "Use the return-printing workflow on each file:",
        "",
        "- group every 4 source surfaces as one return",
        "- offset/join at `0.66 mm`",
        "- produce valid closed solids",
        "- drop to `Z=0` and pack for Bambu",
        "",
        "Object order is preserved for that workflow: `sill`, `head`, `jamb_S`, `jamb_N` for each return ID.",
        "",
        "Band files:",
        "",
    ]
    for band in manifest["bands"]:
        readme_lines.append(
            f"- Band {band['band']:02d} `{band['label']}`: {band['return_count']} returns, "
            f"{band['surface_count']} surfaces, z {band['z0_ft']:.3f}-{band['z1_max_ft']:.3f} ft, "
            f"`{Path(band['file']).name}`"
        )
    (OUT_DIR / "README_WEST_RETURNS_SPLIT_BY_BAND.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")

    print(f"\nWrote manifest: {manifest_path}")
    print(f"Wrote README: {OUT_DIR / 'README_WEST_RETURNS_SPLIT_BY_BAND.md'}")


if __name__ == "__main__":
    main()
