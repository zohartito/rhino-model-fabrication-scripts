import json
import math
import os
import re
import struct
import time
import traceback

import Rhino


IN_DIR = "path/to/west-return-bands"
OUT_DIR = "path/to/west-return-output"

WALL_MM = 0.66
PLATE_X_MM = 250.0
PLATE_Y_MM = 250.0
MARGIN_MM = 5.0
SPACING_MM = 5.0
ORIENT_WEST_OUTER_FACE_FLAT = True


def log(message):
    print(message)
    with open(LOG_PATH, "a") as f:
        f.write(str(message) + "\n")


def safe_stem(path):
    name = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"[^A-Za-z0-9_. -]+", "_", name).strip() or "west_returns"


def source_files():
    names = [
        name
        for name in os.listdir(IN_DIR)
        if name.startswith("west_return_band_") and name.endswith("_SOURCE_SURFACES_mm.3dm")
    ]
    return [os.path.join(IN_DIR, name) for name in sorted(names)]


def model_layer_name(file3dm, obj):
    try:
        return file3dm.Layers[obj.Attributes.LayerIndex].Name
    except Exception:
        return ""


def get_source_breps(file3dm):
    breps = []
    names = []
    for obj in file3dm.Objects:
        geom = obj.Geometry
        if not isinstance(geom, Rhino.Geometry.Brep):
            continue
        if geom.IsSolid:
            continue
        layer_name = model_layer_name(file3dm, obj)
        if layer_name.startswith("CODEX_PRINTABLE"):
            continue
        breps.append(geom.DuplicateBrep())
        names.append(obj.Attributes.Name or "")
    return breps, names


def group_by_object_order(source):
    return [source[i:i + 4] for i in range(0, len(source), 4)]


def join_group(group, tol):
    tolerances = []
    for value in [tol, 0.01, 0.05, 0.1, 0.25]:
        if value not in tolerances:
            tolerances.append(value)
    for join_tol in tolerances:
        joined = Rhino.Geometry.Brep.JoinBreps(group, join_tol)
        joined = list(joined) if joined else []
        if len(joined) == 1:
            return joined[0], join_tol
    return group, tol


def union_solids(parts, tol):
    solids = [part for part in parts if part and part.IsValid and part.IsSolid]
    if len(solids) <= 1:
        return solids
    try:
        result = Rhino.Geometry.Brep.CreateBooleanUnion(solids, tol)
        result = list(result) if result else []
        good = [b for b in result if b and b.IsValid and b.IsSolid]
        if good:
            return good
    except Exception as e:
        log("    BooleanUnion failed, keeping solid parts: {}".format(e))
    return solids


def offset_to_solid(brep, distance, tol):
    for dist in [distance, -distance]:
        result = Rhino.Geometry.Brep.CreateOffsetBrep(brep, dist, True, False, tol)
        if not result:
            continue
        parts = list(result[0]) if isinstance(result, tuple) else list(result)
        good = []
        for part in parts:
            if not part:
                continue
            part.Compact()
            candidates = [part]
            if not part.IsSolid:
                try:
                    capped = part.CapPlanarHoles(tol)
                    if capped:
                        candidates.append(capped)
                except Exception:
                    pass
            for candidate in candidates:
                candidate.Compact()
                if candidate.IsValid and candidate.IsSolid:
                    good.append(candidate)
                    break
        if good:
            return good, dist
    return [], -distance


def drop_to_z_zero(brep):
    bbox = brep.GetBoundingBox(True)
    brep.Transform(Rhino.Geometry.Transform.Translation(0, 0, -bbox.Min.Z))


def orient_west_outer_face_flat(brep):
    # West return source axes:
    #   X = facade depth / normal direction, with the exterior/outer face at min X
    #   Y = horizontal along the West facade
    #   Z = vertical in the model
    # For Bambu printing like the flat panel STLs, put the facade/subframe face
    # on the bed: new X = old Y, new Y = old Z, new Z = old X.
    xform = Rhino.Geometry.Transform(0)
    xform.M00 = 0.0
    xform.M01 = 1.0
    xform.M02 = 0.0
    xform.M03 = 0.0
    xform.M10 = 0.0
    xform.M11 = 0.0
    xform.M12 = 1.0
    xform.M13 = 0.0
    xform.M20 = 1.0
    xform.M21 = 0.0
    xform.M22 = 0.0
    xform.M23 = 0.0
    xform.M30 = 0.0
    xform.M31 = 0.0
    xform.M32 = 0.0
    xform.M33 = 1.0
    brep.Transform(xform)


def footprint(brep):
    bbox = brep.GetBoundingBox(True)
    return bbox.Max.X - bbox.Min.X, bbox.Max.Y - bbox.Min.Y, bbox.Max.Z - bbox.Min.Z


def rotate_around_z_90(brep):
    bbox = brep.GetBoundingBox(True)
    center = Rhino.Geometry.Point3d(
        0.5 * (bbox.Min.X + bbox.Max.X),
        0.5 * (bbox.Min.Y + bbox.Max.Y),
        0,
    )
    brep.Transform(Rhino.Geometry.Transform.Rotation(math.pi / 2.0, Rhino.Geometry.Vector3d.ZAxis, center))


def orient_for_plate(brep):
    usable_x = PLATE_X_MM - 2 * MARGIN_MM
    usable_y = PLATE_Y_MM - 2 * MARGIN_MM
    w, d, _ = footprint(brep)
    if (w > usable_x or d > usable_y) and d <= usable_x and w <= usable_y:
        rotate_around_z_90(brep)
        return True
    return False


def place_solid(brep, target_x, target_y):
    bbox = brep.GetBoundingBox(True)
    brep.Transform(
        Rhino.Geometry.Transform.Translation(
            target_x - bbox.Min.X,
            target_y - bbox.Min.Y,
            -bbox.Min.Z,
        )
    )


def try_place(plate, brep):
    bbox = brep.GetBoundingBox(True)
    w = bbox.Max.X - bbox.Min.X
    d = bbox.Max.Y - bbox.Min.Y
    max_x = PLATE_X_MM - MARGIN_MM
    max_y = PLATE_Y_MM - MARGIN_MM

    if plate["x"] + w <= max_x and plate["y"] + d <= max_y:
        place_solid(brep, plate["x"], plate["y"])
        plate["solids"].append(brep)
        plate["x"] += w + SPACING_MM
        plate["row_h"] = max(plate["row_h"], d)
        return True

    new_y = plate["y"] + plate["row_h"] + SPACING_MM
    if MARGIN_MM + w <= max_x and new_y + d <= max_y:
        plate["x"] = MARGIN_MM
        plate["y"] = new_y
        plate["row_h"] = 0.0
        place_solid(brep, plate["x"], plate["y"])
        plate["solids"].append(brep)
        plate["x"] += w + SPACING_MM
        plate["row_h"] = max(plate["row_h"], d)
        return True

    return False


def center_plate(plate):
    solids = plate["solids"]
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    for brep in solids:
        bbox = brep.GetBoundingBox(True)
        mins[0] = min(mins[0], bbox.Min.X)
        mins[1] = min(mins[1], bbox.Min.Y)
        mins[2] = min(mins[2], bbox.Min.Z)
        maxs[0] = max(maxs[0], bbox.Max.X)
        maxs[1] = max(maxs[1], bbox.Max.Y)
        maxs[2] = max(maxs[2], bbox.Max.Z)
    shift = Rhino.Geometry.Transform.Translation(
        -0.5 * (mins[0] + maxs[0]),
        -0.5 * (mins[1] + maxs[1]),
        -mins[2],
    )
    for brep in solids:
        brep.Transform(shift)
    return mins, maxs


def pack_into_plates(solids):
    prepared = []
    rotated = 0
    for solid in solids:
        brep = solid.DuplicateBrep()
        if ORIENT_WEST_OUTER_FACE_FLAT:
            orient_west_outer_face_flat(brep)
        drop_to_z_zero(brep)
        if orient_for_plate(brep):
            rotated += 1
        prepared.append(brep)

    prepared.sort(key=lambda b: max(footprint(b)[0], footprint(b)[1]), reverse=True)
    plates = []
    oversized = []
    for brep in prepared:
        placed = False
        for plate in plates:
            if try_place(plate, brep):
                placed = True
                break
        if not placed:
            plate = {"solids": [], "x": MARGIN_MM, "y": MARGIN_MM, "row_h": 0.0}
            if not try_place(plate, brep):
                bbox = brep.GetBoundingBox(True)
                oversized.append((bbox.Max.X - bbox.Min.X, bbox.Max.Y - bbox.Min.Y))
                place_solid(brep, MARGIN_MM, MARGIN_MM)
                plate["solids"].append(brep)
            plates.append(plate)

    plate_bounds = []
    for plate in plates:
        mins, maxs = center_plate(plate)
        plate_bounds.append((mins, maxs))
    return plates, rotated, oversized, plate_bounds


def mesh_brep(brep, tol):
    mp = Rhino.Geometry.MeshingParameters()
    mp.Tolerance = max(tol, 0.01)
    mp.MinimumEdgeLength = 0.01
    mp.MaximumEdgeLength = 1.0
    mp.JaggedSeams = False
    mp.RefineGrid = True
    mp.SimplePlanes = True
    meshes = Rhino.Geometry.Mesh.CreateFromBrep(brep, mp)
    clean = []
    if meshes:
        for mesh in meshes:
            if mesh:
                mesh.Normals.ComputeNormals()
                mesh.FaceNormals.ComputeFaceNormals()
                mesh.Compact()
                clean.append(mesh)
    return clean


def point_from_vertex(v):
    return Rhino.Geometry.Point3d(v.X, v.Y, v.Z)


def tri_normal(p0, p1, p2):
    normal = Rhino.Geometry.Vector3d.CrossProduct(p1 - p0, p2 - p0)
    if not normal.Unitize():
        return Rhino.Geometry.Vector3d(0, 0, 0)
    return normal


def iter_mesh_triangles(mesh):
    vertices = mesh.Vertices
    for face in mesh.Faces:
        if face.IsTriangle:
            idxs = [(face.A, face.B, face.C)]
        else:
            idxs = [(face.A, face.B, face.C), (face.A, face.C, face.D)]
        for a, b, c in idxs:
            p0 = point_from_vertex(vertices[a])
            p1 = point_from_vertex(vertices[b])
            p2 = point_from_vertex(vertices[c])
            n = tri_normal(p0, p1, p2)
            yield n, p0, p1, p2


def write_binary_stl(meshes, path):
    triangles = []
    for mesh in meshes:
        triangles.extend(list(iter_mesh_triangles(mesh)))
    header = ("Created by Codex from Rhino west return solids, units are millimeters").ljust(80)[:80]
    with open(path, "wb") as f:
        f.write(header.encode("ascii"))
        f.write(struct.pack("<I", len(triangles)))
        for n, p0, p1, p2 in triangles:
            values = [
                n.X, n.Y, n.Z,
                p0.X, p0.Y, p0.Z,
                p1.X, p1.Y, p1.Z,
                p2.X, p2.Y, p2.Z,
            ]
            f.write(struct.pack("<12fH", *(values + [0])))
    return len(triangles)


def write_plate_3dm(plate, path):
    file3dm = Rhino.FileIO.File3dm()
    file3dm.Settings.ModelUnitSystem = Rhino.UnitSystem.Millimeters
    file3dm.Settings.ModelAbsoluteTolerance = 0.01
    attrs = Rhino.DocObjects.ObjectAttributes()
    attrs.Name = "printable_0p66mm_west_return_solid"
    for brep in plate["solids"]:
        file3dm.Objects.AddBrep(brep, attrs)
    file3dm.Write(path, 8)


def process_file(path):
    stem = safe_stem(path)
    file3dm = Rhino.FileIO.File3dm.Read(path)
    if file3dm is None:
        raise Exception("Could not read {}".format(path))

    tol = max(file3dm.Settings.ModelAbsoluteTolerance, 0.01)
    breps, names = get_source_breps(file3dm)
    groups = group_by_object_order(breps)
    log("\nProcessing {}".format(os.path.basename(path)))
    log("  units: {}".format(file3dm.Settings.ModelUnitSystem))
    log("  source surfaces: {} -> groups: {}".format(len(breps), len(groups)))

    solids = []
    group_reports = []
    for group_index, group in enumerate(groups, start=1):
        if len(group) != 4:
            group_reports.append({"group": group_index, "status": "bad_group_size", "surfaces": len(group)})
            continue
        joined, join_tol = join_group(group, tol)
        if isinstance(joined, list):
            group_solids = []
            for side in joined:
                result, used_dist = offset_to_solid(side, WALL_MM, join_tol)
                group_solids.extend(result)
            result = union_solids(group_solids, join_tol)
            status = "offset_sides_then_union"
        else:
            result, used_dist = offset_to_solid(joined, WALL_MM, join_tol)
            result = union_solids(result, join_tol)
            status = "joined_shell_offset"
        valid = [solid for solid in result if solid and solid.IsValid and solid.IsSolid]
        solids.extend(valid)
        group_reports.append({"group": group_index, "status": status, "valid_solids": len(valid)})

    log("  valid closed solids: {} / {}".format(len(solids), len(groups)))
    if not solids:
        raise Exception("No valid solids from {}".format(path))

    plates, rotated, oversized, plate_bounds = pack_into_plates(solids)
    log("  packed plates: {}, rotated for fit: {}, oversized: {}".format(len(plates), rotated, len(oversized)))

    output_files = []
    for plate_index, plate in enumerate(plates, start=1):
        suffix = "{}_GROUPED_0p66mm_plate{}_PRINT_READY".format(stem, plate_index)
        stl_path = os.path.join(OUT_DIR, suffix + ".stl")
        dm_path = os.path.join(OUT_DIR, suffix + ".3dm")
        meshes = []
        for solid in plate["solids"]:
            meshes.extend(mesh_brep(solid, 0.01))
        tri_count = write_binary_stl(meshes, stl_path)
        write_plate_3dm(plate, dm_path)
        output_files.append(
            {
                "plate": plate_index,
                "stl": stl_path,
                "3dm": dm_path,
                "parts": len(plate["solids"]),
                "mesh_parts": len(meshes),
                "triangles": tri_count,
            }
        )
        log("    plate {}: parts={}, triangles={}, stl={}".format(plate_index, len(plate["solids"]), tri_count, os.path.basename(stl_path)))

    return {
        "source": path,
        "source_surfaces": len(breps),
        "source_groups": len(groups),
        "valid_solids": len(solids),
        "plates": len(plates),
        "rotated_for_fit": rotated,
        "oversized": oversized,
        "group_reports": group_reports,
        "outputs": output_files,
    }


STAMP = time.strftime("%Y%m%d_%H%M%S")
os.makedirs(OUT_DIR, exist_ok=True)
LOG_PATH = os.path.join(OUT_DIR, "west_returns_batch_printable_0p66mm_{}_log.txt".format(STAMP))

try:
    open(LOG_PATH, "w").close()
    log("Batch printable West returns")
    log("Input folder: {}".format(IN_DIR))
    log("Output folder: {}".format(OUT_DIR))
    log("Wall thickness: {} mm".format(WALL_MM))
    log("Orient West outer/subframe face flat on bed: {}".format(ORIENT_WEST_OUTER_FACE_FLAT))
    log("Plate fit box: {} x {} mm".format(PLATE_X_MM, PLATE_Y_MM))

    files = source_files()
    if not files:
        raise Exception("No split source files found in {}".format(IN_DIR))

    manifest = {
        "timestamp": STAMP,
        "input_folder": IN_DIR,
        "output_folder": OUT_DIR,
        "wall_mm": WALL_MM,
        "orientation": "West source axes remapped before packing: print X = source Y, print Y = source Z, print Z = source X, then dropped to Z=0. This puts the West outer/subframe face flat on the bed like the reference panel STLs.",
        "plate_fit_mm": [PLATE_X_MM, PLATE_Y_MM],
        "files": [],
    }
    for path in files:
        manifest["files"].append(process_file(path))

    manifest["total_source_surfaces"] = sum(item["source_surfaces"] for item in manifest["files"])
    manifest["total_groups"] = sum(item["source_groups"] for item in manifest["files"])
    manifest["total_valid_solids"] = sum(item["valid_solids"] for item in manifest["files"])
    manifest["total_stl_plates"] = sum(item["plates"] for item in manifest["files"])

    manifest_path = os.path.join(OUT_DIR, "west_returns_printable_0p66mm_manifest_{}.json".format(STAMP))
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    readme_path = os.path.join(OUT_DIR, "README_WEST_RETURNS_PRINTABLE_0p66mm.md")
    with open(readme_path, "w") as f:
        f.write("# West Returns Printable 0.66 mm\n\n")
        f.write("These files were generated from the West return band source surfaces.\n\n")
        f.write("- Source surfaces were already scaled to physical model millimeters (`1 ft = 12.7 mm`).\n")
        f.write("- Every four surfaces were grouped as one return.\n")
        f.write("- Each return was offset to a closed solid at `0.66 mm` wall thickness.\n")
        f.write("- West orientation is corrected: source `X` depth is printer `Z`, so the outer/subframe face lies flat on the bed.\n")
        f.write("- Solids were dropped to `Z=0`, packed into a `250 x 250 mm` safe Bambu plate box, and exported as STL plus 3DM.\n\n")
        f.write("Summary:\n\n")
        f.write("- Source surfaces: `{}`\n".format(manifest["total_source_surfaces"]))
        f.write("- Return groups: `{}`\n".format(manifest["total_groups"]))
        f.write("- Valid closed solids: `{}`\n".format(manifest["total_valid_solids"]))
        f.write("- STL plates: `{}`\n\n".format(manifest["total_stl_plates"]))
        f.write("Plate files:\n\n")
        for item in manifest["files"]:
            f.write("## {}\n\n".format(os.path.basename(item["source"])))
            f.write("- Returns: `{}`\n".format(item["valid_solids"]))
            f.write("- Plates: `{}`\n".format(item["plates"]))
            for out in item["outputs"]:
                f.write("- Plate {}: `{}` parts, `{}`\n".format(out["plate"], out["parts"], os.path.basename(out["stl"])))
            f.write("\n")
    log("\nManifest: {}".format(manifest_path))
    log("README: {}".format(readme_path))
    log("DONE")
except Exception:
    log(traceback.format_exc())
    raise
