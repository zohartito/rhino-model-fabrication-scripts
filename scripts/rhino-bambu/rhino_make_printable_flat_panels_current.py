import math
import os
import re
import struct
import time
import traceback

import Rhino
import scriptcontext as sc

DEFAULT_MODEL_PATH = "path/to/unsaved-model.3dm"

SOURCE_LAYER = "12_CU_FLAT_SOLID_OPAQUE"
TARGET_THICKNESS_MM = 0.254  # 0.01 in
PLATE_X_MM = 250.0
PLATE_Y_MM = 250.0
MARGIN_MM = 5.0
SPACING_MM = 5.0


def safe_stem(name):
    name = os.path.splitext(name)[0]
    name = re.sub(r"[^A-Za-z0-9_. -]+", "_", name).strip()
    return name or "rhino_flat_panels"


DOC_PATH = sc.doc.Path or DEFAULT_MODEL_PATH
OUT_DIR = os.path.dirname(DOC_PATH)
DOC_STEM = safe_stem(os.path.basename(DOC_PATH))
STAMP = time.strftime("%Y%m%d_%H%M%S")
BASE = "{}_panels_0p01in_{}".format(DOC_STEM, STAMP)
LOG = os.path.join(OUT_DIR, BASE + "_log.txt")
OUTPUT_LAYER_BASE = "CODEX_FLAT_PANELS_0p01in_{}".format(STAMP)


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


def selected_solids():
    solids = []
    for rhino_object in sc.doc.Objects:
        try:
            selected = rhino_object.IsSelected(False) != 0
        except Exception:
            selected = False
        if not selected:
            continue
        layer_name = object_layer_name(rhino_object)
        if layer_name.startswith("CODEX_"):
            continue
        brep = rhino_object.Geometry if isinstance(rhino_object.Geometry, Rhino.Geometry.Brep) else None
        if brep and brep.IsSolid:
            solids.append(brep.DuplicateBrep())
    return solids


def layer_solids():
    solids = []
    for rhino_object in sc.doc.Objects:
        layer_name = object_layer_name(rhino_object)
        if layer_name != SOURCE_LAYER:
            continue
        brep = rhino_object.Geometry if isinstance(rhino_object.Geometry, Rhino.Geometry.Brep) else None
        if brep and brep.IsSolid:
            solids.append(brep.DuplicateBrep())
    return solids


def get_source_solids():
    solids = selected_solids()
    if solids:
        log("Using selected closed panel solids: {}".format(len(solids)))
        return solids
    solids = layer_solids()
    log("Using source-layer closed panel solids: {}".format(len(solids)))
    return solids


def bbox_dims(brep):
    bbox = brep.GetBoundingBox(True)
    return (
        bbox.Max.X - bbox.Min.X,
        bbox.Max.Y - bbox.Min.Y,
        bbox.Max.Z - bbox.Min.Z,
    )


def scale_z_to_thickness(brep, target_model_units):
    bbox = brep.GetBoundingBox(True)
    current = bbox.Max.Z - bbox.Min.Z
    if current <= 1e-9:
        log("WARNING: panel has near-zero Z thickness; leaving thickness unchanged")
        return current, 1.0
    factor = target_model_units / current
    xform = Rhino.Geometry.Transform(1, 0, 0, 0,
                                     0, 1, 0, 0,
                                     0, 0, factor, bbox.Min.Z * (1.0 - factor),
                                     0, 0, 0, 1)
    brep.Transform(xform)
    return current, factor


def scale_to_mm(brep):
    mm_per_model_unit = Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)
    duplicate = brep.DuplicateBrep()
    if abs(mm_per_model_unit - 1.0) > 1e-9:
        duplicate.Transform(Rhino.Geometry.Transform.Scale(Rhino.Geometry.Point3d.Origin, mm_per_model_unit))
    return duplicate


def drop_to_z_zero(brep):
    bbox = brep.GetBoundingBox(True)
    brep.Transform(Rhino.Geometry.Transform.Translation(0, 0, -bbox.Min.Z))


def rotate_around_z_90(brep):
    bbox = brep.GetBoundingBox(True)
    center = Rhino.Geometry.Point3d(
        0.5 * (bbox.Min.X + bbox.Max.X),
        0.5 * (bbox.Min.Y + bbox.Max.Y),
        0,
    )
    brep.Transform(Rhino.Geometry.Transform.Rotation(math.pi / 2.0, Rhino.Geometry.Vector3d.ZAxis, center))


def orient_for_plate(brep):
    usable_x = PLATE_X_MM - 2.0 * MARGIN_MM
    usable_y = PLATE_Y_MM - 2.0 * MARGIN_MM
    w, d, _ = bbox_dims(brep)
    if (w > usable_x or d > usable_y) and d <= usable_x and w <= usable_y:
        rotate_around_z_90(brep)
        log("Rotated one panel 90 degrees for plate fit")


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

    prepared.sort(key=lambda b: max(bbox_dims(b)[0], bbox_dims(b)[1]), reverse=True)
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
                log("WARNING: panel footprint may exceed plate: {:.3f} x {:.3f} mm".format(
                    bbox.Max.X - bbox.Min.X, bbox.Max.Y - bbox.Min.Y
                ))
                place_solid(brep, MARGIN_MM, MARGIN_MM)
                plate["solids"].append(brep)
            plates.append(plate)

    for i, plate in enumerate(plates):
        mins, maxs = center_plate(plate)
        log("Plate {} has {} panels; pre-center bounds min=({:.3f},{:.3f},{:.3f}) max=({:.3f},{:.3f},{:.3f})".format(
            i + 1,
            len(plate["solids"]),
            mins[0], mins[1], mins[2],
            maxs[0], maxs[1], maxs[2],
        ))
    return plates


def mesh_brep(brep, tol):
    mp = Rhino.Geometry.MeshingParameters()
    mp.Tolerance = max(tol, 0.005)
    mp.MinimumEdgeLength = 0.005
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
    header = ("Created by Codex from Rhino flat panels, units are millimeters").ljust(80)[:80]
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


def write_3dm(path, solids, object_name):
    file3dm = Rhino.FileIO.File3dm()
    file3dm.Settings.ModelUnitSystem = Rhino.UnitSystem.Millimeters
    file3dm.Settings.ModelAbsoluteTolerance = 0.01
    attrs = Rhino.DocObjects.ObjectAttributes()
    attrs.Name = object_name
    for brep in solids:
        file3dm.Objects.AddBrep(brep, attrs)
    file3dm.Write(path, 8)


try:
    import System.Drawing

    open(LOG, "w").close()
    doc_tol = sc.doc.ModelAbsoluteTolerance
    mm_per_model_unit = Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)
    target_model_units = TARGET_THICKNESS_MM / mm_per_model_unit
    log("Making printable flat envelope panels")
    log("Document path: {}".format(DOC_PATH))
    log("Document units: {}".format(sc.doc.ModelUnitSystem))
    log("Document tolerance: {}".format(doc_tol))
    log("Target panel thickness: {:.6f} mm = {:.6f} model units = 0.01 in".format(
        TARGET_THICKNESS_MM, target_model_units
    ))
    log("Plate fit box: {} x {} mm".format(PLATE_X_MM, PLATE_Y_MM))

    source = get_source_solids()
    log("Source closed panel solids found: {}".format(len(source)))
    if not source:
        raise Exception("No source closed Breps found.")

    source_mm = [scale_to_mm(brep) for brep in source]
    reference_path = os.path.join(OUT_DIR, "{}_SOURCE_REFERENCE_MODEL_POSITION.3dm".format(BASE))
    write_3dm(reference_path, source_mm, "source_reference_panel_solid")
    log("Source reference 3DM: {}".format(reference_path))

    adjusted = []
    for i, solid in enumerate(source):
        brep = solid.DuplicateBrep()
        dims_before = bbox_dims(brep)
        current, factor = scale_z_to_thickness(brep, target_model_units)
        dims_after = bbox_dims(brep)
        log("Panel {}: Z thickness {:.6f} -> {:.6f} model units, scale factor {:.6f}; dims before {}, after {}".format(
            i + 1,
            current,
            dims_after[2],
            factor,
            tuple(round(x, 6) for x in dims_before),
            tuple(round(x, 6) for x in dims_after),
        ))
        if brep.IsValid and brep.IsSolid:
            adjusted.append(brep)
        else:
            log("WARNING: adjusted panel {} is not a valid closed solid".format(i + 1))

    log("Valid adjusted solids: {} / {}".format(len(adjusted), len(source)))
    if not adjusted:
        raise Exception("Thickness adjustment produced no valid closed solids.")

    plates = pack_into_plates(adjusted)
    log("Packed plates: {}".format(len(plates)))

    for plate_index, plate in enumerate(plates):
        layer_name = "{}_plate{}".format(OUTPUT_LAYER_BASE, plate_index + 1)
        layer_color = System.Drawing.Color.FromArgb(
            255,
            130 + (plate_index * 40) % 100,
            10 + (plate_index * 40) % 180,
        )
        layer_index = ensure_layer(layer_name, layer_color)
        attrs = Rhino.DocObjects.ObjectAttributes()
        attrs.LayerIndex = layer_index
        attrs.Name = "flat_panel_0p01in_plate{}".format(plate_index + 1)
        for solid in plate["solids"]:
            sc.doc.Objects.AddBrep(solid, attrs)

        meshes = []
        for solid in plate["solids"]:
            meshes.extend(mesh_brep(solid, 0.005))

        suffix = "plate{}_centered".format(plate_index + 1)
        stl_path = os.path.join(OUT_DIR, "{}_{}.stl".format(BASE, suffix))
        dm_path = os.path.join(OUT_DIR, "{}_{}.3dm".format(BASE, suffix))
        tri_count = write_binary_stl(meshes, stl_path)
        write_3dm(dm_path, plate["solids"], "flat_panel_0p01in_print_ready")
        log("Plate {} mesh parts: {}".format(plate_index + 1, len(meshes)))
        log("Plate {} STL triangles: {}".format(plate_index + 1, tri_count))
        log("Plate {} STL: {}".format(plate_index + 1, stl_path))
        log("Plate {} 3DM: {}".format(plate_index + 1, dm_path))

    sc.doc.Views.Redraw()
    log("DONE")
except Exception:
    log(traceback.format_exc())
