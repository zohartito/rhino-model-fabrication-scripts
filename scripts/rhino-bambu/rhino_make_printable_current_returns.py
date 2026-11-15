import math
import os
import re
import struct
import time
import traceback

import Rhino
import scriptcontext as sc

DEFAULT_MODEL_PATH = "path/to/unsaved-model.3dm"

SOURCE_LAYER = "15_CU_PUNCH_RETURNS_SOUTH_BAY_ALIGNED_V44"
WALL_MM = 0.66
PLATE_X_MM = 250.0
PLATE_Y_MM = 250.0
MARGIN_MM = 5.0
SPACING_MM = 5.0
GROUP_BY_OBJECT_ORDER = True
FORCE_INDIVIDUAL_SURFACES = False


def safe_stem(name):
    name = os.path.splitext(name)[0]
    name = re.sub(r"[^A-Za-z0-9_. -]+", "_", name).strip()
    return name or "rhino_returns"


DOC_PATH = sc.doc.Path or DEFAULT_MODEL_PATH
OUT_DIR = os.path.dirname(DOC_PATH)
DOC_STEM = safe_stem(os.path.basename(DOC_PATH))
STAMP = time.strftime("%Y%m%d_%H%M%S")
BASE = "{}_printable_0p66mm_{}".format(DOC_STEM, STAMP)
LOG = os.path.join(OUT_DIR, BASE + "_log.txt")
OUTPUT_LAYER_BASE = "CODEX_PRINTABLE_0p66mm_{}".format(STAMP)


def log(message):
    with open(LOG, "a") as f:
        f.write(str(message) + "\n")
    print(message)


def ensure_layer(name, color):
    index = sc.doc.Layers.FindByFullPath(name, True)
    if index >= 0:
        return index
    layer = Rhino.DocObjects.Layer()
    layer.Name = name
    layer.Color = color
    return sc.doc.Layers.Add(layer)


def object_layer_name(rhino_object):
    layer = sc.doc.Layers[rhino_object.Attributes.LayerIndex]
    return layer.Name if layer else ""


def selected_breps():
    breps = []
    for rhino_object in sc.doc.Objects:
        try:
            selected = rhino_object.IsSelected(False) != 0
        except Exception:
            selected = False
        if not selected:
            continue
        if object_layer_name(rhino_object).startswith("CODEX_PRINTABLE"):
            continue
        brep = rhino_object.Geometry if isinstance(rhino_object.Geometry, Rhino.Geometry.Brep) else None
        if brep and not brep.IsSolid:
            breps.append(brep.DuplicateBrep())
    return breps


def layer_breps():
    breps = []
    for rhino_object in sc.doc.Objects:
        if object_layer_name(rhino_object) != SOURCE_LAYER:
            continue
        brep = rhino_object.Geometry if isinstance(rhino_object.Geometry, Rhino.Geometry.Brep) else None
        if brep and not brep.IsSolid:
            breps.append(brep.DuplicateBrep())
    return breps


def get_source_breps():
    breps = selected_breps()
    if breps:
        log("Using selected open breps: {}".format(len(breps)))
        return breps
    breps = layer_breps()
    log("Using source-layer open breps: {}".format(len(breps)))
    return breps


def group_by_object_order(source):
    groups = []
    for i in range(0, len(source), 4):
        groups.append(source[i:i + 4])
    return groups


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
        log("BooleanUnion failed, keeping joined solid parts: {}".format(e))
    return solids


def join_source_breps(source, base_tol):
    expected = int(len(source) / 4) if len(source) and len(source) % 4 == 0 else None
    tolerances = []
    for tol in [base_tol, 0.01, 0.05, 0.1, 0.25]:
        if tol not in tolerances:
            tolerances.append(tol)

    best_joined = []
    best_tol = base_tol
    best_score = None
    for tol in tolerances:
        joined = Rhino.Geometry.Brep.JoinBreps(source, tol)
        joined = list(joined) if joined else []
        face_counts = [b.Faces.Count for b in joined]
        log("Join tol {} -> {} shells, face counts {}".format(
            tol, len(joined), face_counts[:12] + (["..."] if len(face_counts) > 12 else [])
        ))
        if not joined:
            continue
        if expected:
            score = abs(len(joined) - expected)
            if len(joined) == expected:
                return joined, tol
        else:
            score = len(joined)
        if best_score is None or score < best_score:
            best_joined = joined
            best_tol = tol
            best_score = score
    return best_joined, best_tol


def offset_to_solid(brep, distance, tol):
    attempts = [distance, -distance]
    last = []
    for dist in attempts:
        result = Rhino.Geometry.Brep.CreateOffsetBrep(brep, dist, True, False, tol)
        if not result:
            continue
        parts = list(result[0]) if isinstance(result, tuple) else list(result)
        last = parts
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
    return last, attempts[-1]


def scale_to_mm(brep):
    mm_per_model_unit = Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)
    duplicate = brep.DuplicateBrep()
    if abs(mm_per_model_unit - 1.0) > 1e-9:
        duplicate.Transform(Rhino.Geometry.Transform.Scale(Rhino.Geometry.Point3d.Origin, mm_per_model_unit))
    return duplicate


def drop_to_z_zero(brep):
    bbox = brep.GetBoundingBox(True)
    brep.Transform(Rhino.Geometry.Transform.Translation(0, 0, -bbox.Min.Z))


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
        log("Rotated one return 90 degrees for plate fit")


def place_solid(brep, target_x, target_y):
    bbox = brep.GetBoundingBox(True)
    brep.Transform(Rhino.Geometry.Transform.Translation(
        target_x - bbox.Min.X,
        target_y - bbox.Min.Y,
        -bbox.Min.Z,
    ))


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
    for solid in solids:
        brep = scale_to_mm(solid)
        drop_to_z_zero(brep)
        orient_for_plate(brep)
        prepared.append(brep)

    prepared.sort(key=lambda b: max(footprint(b)[0], footprint(b)[1]), reverse=True)
    plates = []
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
                log("WARNING: return footprint may exceed plate: {:.3f} x {:.3f} mm".format(
                    bbox.Max.X - bbox.Min.X, bbox.Max.Y - bbox.Min.Y
                ))
                place_solid(brep, MARGIN_MM, MARGIN_MM)
                plate["solids"].append(brep)
            plates.append(plate)

    for i, plate in enumerate(plates):
        mins, maxs = center_plate(plate)
        log("Plate {} has {} returns; pre-center bounds min=({:.3f},{:.3f},{:.3f}) max=({:.3f},{:.3f},{:.3f})".format(
            i + 1,
            len(plate["solids"]),
            mins[0], mins[1], mins[2],
            maxs[0], maxs[1], maxs[2],
        ))
    return plates


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
    header = ("Created by Codex from Rhino solids, units are millimeters").ljust(80)[:80]
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
    attrs.Name = "printable_0p66mm_solid"
    for brep in plate["solids"]:
        file3dm.Objects.AddBrep(brep, attrs)
    file3dm.Write(path, 8)


try:
    import System.Drawing

    open(LOG, "w").close()
    doc_tol = sc.doc.ModelAbsoluteTolerance
    offset_model_units = WALL_MM * Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, sc.doc.ModelUnitSystem)
    log("Making printable returns")
    log("Document path: {}".format(DOC_PATH))
    log("Document units: {}".format(sc.doc.ModelUnitSystem))
    log("Document tolerance: {}".format(doc_tol))
    log("Wall thickness: {} mm = {} model units".format(WALL_MM, offset_model_units))
    log("Plate fit box: {} x {} mm".format(PLATE_X_MM, PLATE_Y_MM))

    source = get_source_breps()
    log("Source open breps found: {}".format(len(source)))
    if not source:
        raise Exception("No source open Breps found.")

    if GROUP_BY_OBJECT_ORDER:
        grouped = group_by_object_order(source)
        joined = []
        join_tol = doc_tol
        log("Object-order grouped mode: {} groups of four source surfaces".format(len(grouped)))
        for group_index, group in enumerate(grouped):
            if len(group) != 4:
                log("WARNING: group {} has {} surfaces, expected 4".format(group_index + 1, len(group)))
            joined_group = Rhino.Geometry.Brep.JoinBreps(group, doc_tol)
            joined_group = list(joined_group) if joined_group else []
            if len(joined_group) == 1:
                joined.append(joined_group[0])
                log("Group {} joined into one 4-side shell".format(group_index + 1))
            else:
                log("Group {} did not join cleanly at tol {}; offsetting its {} sides then BooleanUnion".format(
                    group_index + 1, doc_tol, len(group)
                ))
                joined.append(group)
    elif FORCE_INDIVIDUAL_SURFACES:
        joined = source
        join_tol = doc_tol
        log("Individual-surface mode: offsetting each source surface separately to preserve all pieces")
    else:
        joined, join_tol = join_source_breps(source, doc_tol)
        log("Joined return shells selected: {} at tolerance {}".format(len(joined), join_tol))

    solids = []
    for i, shell in enumerate(joined):
        if isinstance(shell, list):
            group_solids = []
            for side_index, side in enumerate(shell):
                log("Return {} side {} input: faces={}, edges={}, solid={}, valid={}".format(
                    i + 1, side_index + 1, side.Faces.Count, side.Edges.Count, side.IsSolid, side.IsValid
                ))
                result, used_dist = offset_to_solid(side, offset_model_units, join_tol)
                log("Return {} side {} offset used distance: {}".format(i + 1, side_index + 1, used_dist))
                for solid in result:
                    if solid and solid.IsValid and solid.IsSolid:
                        group_solids.append(solid)
            unioned = union_solids(group_solids, join_tol)
            log("Return {} BooleanUnion/solid group parts: {}".format(i + 1, len(unioned)))
            for j, solid in enumerate(unioned):
                log("Return {}.{} output: faces={}, edges={}, solid={}, valid={}".format(
                    i + 1, j + 1, solid.Faces.Count, solid.Edges.Count, solid.IsSolid, solid.IsValid
                ))
                solids.append(solid)
            continue

        log("Return {} input: faces={}, edges={}, solid={}, valid={}".format(
            i + 1, shell.Faces.Count, shell.Edges.Count, shell.IsSolid, shell.IsValid
        ))
        result, used_dist = offset_to_solid(shell, offset_model_units, join_tol)
        result = union_solids(result, join_tol)
        log("Return {} offset used distance: {}".format(i + 1, used_dist))
        log("Return {} offset/union parts: {}".format(i + 1, len(result)))
        for j, solid in enumerate(result):
            if not solid:
                continue
            log("Return {}.{} output: faces={}, edges={}, solid={}, valid={}".format(
                i + 1, j + 1, solid.Faces.Count, solid.Edges.Count, solid.IsSolid, solid.IsValid
            ))
            if solid.IsValid and solid.IsSolid:
                solids.append(solid)

    log("Valid closed solids: {} / {}".format(len(solids), len(joined)))
    if not solids:
        raise Exception("Offset produced no valid closed solids.")

    plates = pack_into_plates(solids)
    log("Packed plates: {}".format(len(plates)))

    stl_paths = []
    for plate_index, plate in enumerate(plates):
        layer_name = "{}_plate{}".format(OUTPUT_LAYER_BASE, plate_index + 1)
        layer_color = System.Drawing.Color.FromArgb(
            0,
            70 + (plate_index * 40) % 160,
            255 - (plate_index * 30) % 160,
        )
        layer_index = ensure_layer(layer_name, layer_color)
        attrs = Rhino.DocObjects.ObjectAttributes()
        attrs.LayerIndex = layer_index
        attrs.Name = "printable_0p66mm_solid_plate{}".format(plate_index + 1)
        for solid in plate["solids"]:
            sc.doc.Objects.AddBrep(solid, attrs)

        meshes = []
        for solid in plate["solids"]:
            meshes.extend(mesh_brep(solid, 0.01))

        suffix = "plate{}_centered".format(plate_index + 1)
        stl_path = os.path.join(OUT_DIR, "{}_{}.stl".format(BASE, suffix))
        dm_path = os.path.join(OUT_DIR, "{}_{}.3dm".format(BASE, suffix))
        tri_count = write_binary_stl(meshes, stl_path)
        write_plate_3dm(plate, dm_path)
        stl_paths.append(stl_path)
        log("Plate {} mesh parts: {}".format(plate_index + 1, len(meshes)))
        log("Plate {} STL triangles: {}".format(plate_index + 1, tri_count))
        log("Plate {} STL: {}".format(plate_index + 1, stl_path))
        log("Plate {} 3DM: {}".format(plate_index + 1, dm_path))

    sc.doc.Views.Redraw()
    log("DONE")
except Exception:
    log(traceback.format_exc())
