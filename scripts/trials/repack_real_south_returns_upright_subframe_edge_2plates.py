import json
import pathlib

import trimesh


SOURCE_DIR = pathlib.Path(
    "path/to/flat-face-return-stls"
)
OUT_DIR = pathlib.Path(
    "path/to/upright-return-output"
)

MARGIN_MM = 2.0
SPACING_MM = 2.0


def normalize(mesh):
    mesh = mesh.copy()
    bounds = mesh.bounds
    mesh.apply_translation([-bounds[0, 0], -bounds[0, 1], -bounds[0, 2]])
    return mesh


def laid_flat_to_upright(mesh):
    mesh = normalize(mesh)
    # Previous mistaken files had:
    #   X = facade length, Y = model height, Z = return/subframe depth.
    # Correct printed South-return orientation has:
    #   X = facade length, Y = skinny return/subframe depth on the bed, Z = model height.
    # So swap Y and Z, then repair winding if the reflected transform inverted normals.
    transform = [
        [1, 0, 0, 0],
        [0, 0, 1, 0],
        [0, 1, 0, 0],
        [0, 0, 0, 1],
    ]
    mesh.apply_transform(transform)
    mesh = normalize(mesh)
    if mesh.is_watertight:
        trimesh.repair.fix_normals(mesh)
        if mesh.volume < 0:
            mesh.invert()
    return mesh


def place(mesh, x, y):
    mesh = normalize(mesh)
    mesh.apply_translation([x, y, 0])
    return mesh


def center_for_bambu(mesh):
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
        for component_index, component in enumerate(mesh.split(only_watertight=False), start=1):
            upright = laid_flat_to_upright(component)
            dims = upright.extents
            parts.append(
                {
                    "source": stl.name,
                    "component": component_index,
                    "mesh": upright,
                    "dims_mm": [float(dims[0]), float(dims[1]), float(dims[2])],
                }
            )
    if len(parts) != 12:
        raise RuntimeError(f"Expected 12 return solids, found {len(parts)}")
    return parts


def export_plate(plate_number, placed_parts):
    combined = trimesh.util.concatenate([part["mesh"] for part in placed_parts])
    combined = center_for_bambu(combined)
    out = OUT_DIR / (
        "REAL_3RD_TO_BOTTOM_SOUTH_RETURNS_CORRECT_UPRIGHT_SUBFRAME_EDGE_ON_BED_"
        f"0p66mm_plate{plate_number}_PRINT_READY.stl"
    )
    combined.export(out)
    checked = trimesh.load_mesh(out, process=True)
    return {
        "plate": plate_number,
        "stl": str(out),
        "parts": len(checked.split(only_watertight=False)),
        "watertight": bool(checked.is_watertight),
        "winding_consistent": bool(checked.is_winding_consistent),
        "min_z_mm": float(checked.bounds[0, 2]),
        "dims_mm": [float(v) for v in checked.extents],
        "placements": [
            {
                "source": part["source"],
                "component": part["component"],
                "x_mm": part["x_mm"],
                "y_mm": part["y_mm"],
                "original_upright_dims_mm": part["dims_mm"],
            }
            for part in placed_parts
        ],
    }


def stack_column(parts):
    y = MARGIN_MM
    placed = []
    for part in parts:
        mesh = place(part["mesh"], MARGIN_MM, y)
        item = dict(part)
        item["mesh"] = mesh
        item["x_mm"] = MARGIN_MM
        item["y_mm"] = y
        placed.append(item)
        y += float(mesh.extents[1]) + SPACING_MM
    return placed


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parts = load_parts()
    parts = sorted(parts, key=lambda item: item["dims_mm"][0], reverse=True)

    long_returns = parts[:6]
    short_returns = parts[6:]

    summaries = [
        export_plate(1, stack_column(long_returns)),
        export_plate(2, stack_column(short_returns)),
    ]

    issues = [
        summary["stl"]
        for summary in summaries
        if not summary["watertight"] or abs(summary["min_z_mm"]) > 1e-6
    ]

    manifest = {
        "source_folder": str(SOURCE_DIR),
        "output_folder": str(OUT_DIR),
        "orientation": (
            "Matches printed South return example: returns stand upright; "
            "X is facade length, Y is skinny return/subframe depth on the bed, "
            "Z is model height. The bed side is the subframe-contact edge/base, "
            "not the broad return face."
        ),
        "plate_count": 2,
        "margin_mm": MARGIN_MM,
        "spacing_mm": SPACING_MM,
        "plates": summaries,
        "verification_issues": issues,
    }
    (OUT_DIR / "REAL_3RD_TO_BOTTOM_SOUTH_RETURNS_CORRECT_UPRIGHT_manifest.json").write_text(
        json.dumps(manifest, indent=2)
    )

    readme_lines = [
        "# REAL 3rd To Bottom South Returns - Correct Upright Orientation",
        "",
        "Use these STLs, not the laid-flat versions.",
        "",
        "Orientation matches the printed South return reference:",
        "",
        "- Returns stand upright.",
        "- `Z` is the model height.",
        "- The skinny subframe-contact edge/base is on the print bed.",
        "- The broad return face is not lying flat on the bed.",
        "- Wall thickness is `0.66 mm`.",
        "",
        "Files:",
        "",
    ]
    for summary in summaries:
        dims = [round(v, 3) for v in summary["dims_mm"]]
        readme_lines.append(
            f"- Plate {summary['plate']}: {summary['parts']} parts, "
            f"watertight={summary['watertight']}, min_z={summary['min_z_mm']:.6f}, "
            f"dims={dims} mm, `{pathlib.Path(summary['stl']).name}`"
        )
    readme_lines.append("")
    readme_lines.append(f"Verification issues: `{issues}`")
    (OUT_DIR / "README_CORRECT_UPRIGHT_SUBFRAME_EDGE_ON_BED.md").write_text(
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
