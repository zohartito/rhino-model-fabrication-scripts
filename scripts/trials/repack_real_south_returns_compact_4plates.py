import json
import pathlib

import numpy as np
import trimesh


SOURCE_DIR = pathlib.Path(
    "path/to/flat-face-return-stls"
)
OUT_DIR = pathlib.Path(
    "path/to/compact-return-output"
)

MARGIN = 2.0
SPACING = 2.0


def normalize_component(mesh):
    mesh = mesh.copy()
    bounds = mesh.bounds
    mesh.apply_translation([-bounds[0, 0], -bounds[0, 1], -bounds[0, 2]])
    return mesh


def placed(mesh, x, y):
    mesh = normalize_component(mesh)
    mesh.apply_translation([x, y, 0])
    return mesh


def center_on_origin(mesh):
    bounds = mesh.bounds
    mesh.apply_translation(
        [
            -0.5 * (bounds[0, 0] + bounds[1, 0]),
            -0.5 * (bounds[0, 1] + bounds[1, 1]),
            -bounds[0, 2],
        ]
    )
    return mesh


def load_parts():
    parts = []
    for stl in sorted(SOURCE_DIR.glob("*_PRINT_READY.stl")):
        mesh = trimesh.load_mesh(stl, process=True)
        for index, component in enumerate(mesh.split(only_watertight=False), start=1):
            component = normalize_component(component)
            dims = component.extents
            parts.append(
                {
                    "source": stl.name,
                    "component": index,
                    "mesh": component,
                    "dims": [float(dims[0]), float(dims[1]), float(dims[2])],
                }
            )
    if len(parts) != 12:
        raise RuntimeError(f"Expected 12 return parts, found {len(parts)}")
    return parts


def make_plate(plate_number, items):
    meshes = [item["mesh"] for item in items]
    combined = trimesh.util.concatenate(meshes)
    combined = center_on_origin(combined)
    output = OUT_DIR / (
        "REAL_3RD_TO_BOTTOM_SOUTH_RETURNS_COMPACT_PACKED_FLAT_OUTER_FACE_"
        f"0p66mm_plate{plate_number}_PRINT_READY.stl"
    )
    combined.export(output)
    checked = trimesh.load_mesh(output, process=True)
    components = checked.split(only_watertight=False)
    return {
        "plate": plate_number,
        "stl": str(output),
        "parts": len(components),
        "watertight": bool(checked.is_watertight),
        "winding_consistent": bool(checked.is_winding_consistent),
        "min_z_mm": float(checked.bounds[0, 2]),
        "dims_mm": [float(v) for v in checked.extents],
        "sources": [
            {
                "source": item["source"],
                "component": item["component"],
                "placement": item["placement"],
                "original_dims_mm": item["dims"],
            }
            for item in items
        ],
    }


def no_bbox_overlaps(meshes):
    boxes = []
    for mesh in meshes:
        bounds = mesh.bounds
        boxes.append((bounds[0, 0], bounds[0, 1], bounds[1, 0], bounds[1, 1]))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            separated = (
                a[2] + 1e-6 <= b[0]
                or b[2] + 1e-6 <= a[0]
                or a[3] + 1e-6 <= b[1]
                or b[3] + 1e-6 <= a[1]
            )
            if not separated:
                return False
    return True


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parts = load_parts()

    # Keep the three long-return pairings from the verified set. They already
    # fit correctly. The actual compaction is merging the six short returns onto
    # one plate. Every move is an XY translation only, so the bed-facing outer
    # subframe side is preserved.
    groups = [
        parts[0:2],
        parts[2:4],
        parts[4:6],
        parts[6:12],
    ]

    summaries = []
    for plate_index, group in enumerate(groups, start=1):
        if plate_index < 4:
            y = MARGIN
            placed_items = []
            for item in group:
                mesh = placed(item["mesh"], MARGIN, y)
                item = dict(item)
                item["mesh"] = mesh
                item["placement"] = {"x_mm": MARGIN, "y_mm": y, "z_rotation_deg": 0}
                placed_items.append(item)
                y += mesh.extents[1] + SPACING
        else:
            long_items = sorted(group[:4], key=lambda item: item["dims"][1], reverse=True)
            short_items = sorted(group[4:], key=lambda item: item["dims"][1], reverse=True)
            placed_items = []
            y = MARGIN
            left_width = max(item["dims"][0] for item in long_items)
            for item in long_items:
                mesh = placed(item["mesh"], MARGIN, y)
                item = dict(item)
                item["mesh"] = mesh
                item["placement"] = {"x_mm": MARGIN, "y_mm": y, "z_rotation_deg": 0}
                placed_items.append(item)
                y += mesh.extents[1] + SPACING
            x_right = MARGIN + left_width + SPACING
            y = MARGIN
            for item in short_items:
                mesh = placed(item["mesh"], x_right, y)
                item = dict(item)
                item["mesh"] = mesh
                item["placement"] = {"x_mm": x_right, "y_mm": y, "z_rotation_deg": 0}
                placed_items.append(item)
                y += mesh.extents[1] + SPACING

        if not no_bbox_overlaps([item["mesh"] for item in placed_items]):
            raise RuntimeError(f"Bounding boxes overlap on plate {plate_index}")
        summaries.append(make_plate(plate_index, placed_items))

    issues = [
        s["stl"]
        for s in summaries
        if (not s["watertight"]) or abs(s["min_z_mm"]) > 1e-6
    ]
    manifest = {
        "source_folder": str(SOURCE_DIR),
        "output_folder": str(OUT_DIR),
        "note": (
            "Compact repack of the already verified flat-outer-face STLs. "
            "Only XY translations were used, so the outer/subframe face stays "
            "flat on the bed."
        ),
        "margin_mm": MARGIN,
        "spacing_mm": SPACING,
        "plate_count": len(summaries),
        "plates": summaries,
        "verification_issues": issues,
    }
    (OUT_DIR / "REAL_3RD_TO_BOTTOM_SOUTH_RETURNS_COMPACT_PACKED_manifest.json").write_text(
        json.dumps(manifest, indent=2)
    )
    readme_lines = [
        "# REAL 3rd To Bottom South Returns - Compact Packed",
        "",
        "Use this folder when you want fewer Bambu plates.",
        "",
        "- Source: already verified flat-outer-face 0.66 mm STL set",
        "- Repack method: XY translations only",
        "- Bed-facing side: unchanged, outer/subframe face stays flat on the plate",
        "- Plate count: 4",
        "- Spacing: 2 mm",
        "",
    ]
    for summary in summaries:
        dims = [round(v, 3) for v in summary["dims_mm"]]
        readme_lines.append(
            f"- Plate {summary['plate']}: {summary['parts']} parts, "
            f"watertight={summary['watertight']}, min_z={summary['min_z_mm']:.6f}, dims={dims} mm, "
            f"`{pathlib.Path(summary['stl']).name}`"
        )
    readme_lines.append("")
    readme_lines.append(f"Verification issues: `{issues}`")
    (OUT_DIR / "README_COMPACT_PACKED_FLAT_OUTER_FACE.md").write_text(
        "\n".join(readme_lines) + "\n"
    )
    for summary in summaries:
        print(
            f"plate {summary['plate']}: parts={summary['parts']} "
            f"watertight={summary['watertight']} min_z={summary['min_z_mm']:.6f} "
            f"dims={[round(v, 3) for v in summary['dims_mm']]}"
        )
    print("issues", issues)
    print("out", OUT_DIR)


if __name__ == "__main__":
    main()
