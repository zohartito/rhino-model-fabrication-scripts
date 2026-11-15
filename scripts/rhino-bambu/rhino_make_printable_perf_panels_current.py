import math
import os
import re
import struct
import time
import traceback

import Rhino
import scriptcontext as sc


DEFAULT_MODEL_PATH = "path/to/unsaved-model.3dm"
SUMMARY_DIR = "path/to/logs"

SOURCE_LAYER = "13_CU_FLAT_PERF_TRANSLUCENT"
TARGETS = [
    ("0p01in", 0.254, "literal 0.01 in"),
    ("MATCH_SOUTH_STL", 0.5705078125, "matches measured South STL panel thickness"),
]
PLATE_X_MM = 250.0
PLATE_Y_MM = 250.0
MARGIN_MM = 5.0
SPACING_MM = 5.0


def safe_stem(name):
    name = os.path.splitext(name)[0].strip()
    name = re.sub(r"[^A-Za-z0-9_. -]+", "_", name).strip()
    return name or "rhino_perf_panels"


DOC_PATH = sc.doc.Path or DEFAULT_MODEL_PATH
OUT_DIR = os.path.dirname(DOC_PATH)
DOC_STEM = safe_stem(os.path.basename(DOC_PATH))
STAMP = time.strftime("%Y%m%d_%H%M%S")
SUMMARY_PATH = os.path.join(SUMMARY_DIR, "{}_summary_from_rhino.txt".format(
    re.sub(r"[^A-Za-z0-9_.-]+", "_", DOC_STEM)
))


def write_text(path, text):
    with open(path, "a") as handle:
        handle.write(str(text) + "\n")


def object_layer_name(rhino_object):
    layer = sc.doc.Layers[rhino_object.Attributes.LayerIndex]
    return layer.Name if layer else ""


def source_solids():
    solids = []
    for rhino_object in sc.doc.Objects:
        if object_layer_name(rhino_object) != SOURCE_LAYER:
            continue
        brep = rhino_object.Geometry if isinstance(rhino_object.Geometry, Rhino.Geometry.Brep) else None
        if brep and brep.IsSolid and brep.IsValid:
            solids.append(brep.DuplicateBrep())
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
        return current, 1.0
    factor = target_model_units / current
    xform = Rhino.Geometry.Transform.Identity
    xform.M22 = factor
    xform.M23 = bbox.Min.Z * (1.0 - factor)
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
    width, depth, _ = bbox_dims(brep)
    if (width > usable_x or depth > usable_y) and depth <= usable_x and width <= usable_y:
        rotate_around_z_90(brep)


def place_solid(brep, target_x, target_y):
    bbox = brep.GetBoundingBox(True)
    brep.Transform(Rhino.Geometry.Transform.Translation(
        target_x - bbox.Min.X,
        target_y - bbox.Min.Y,
        -bbox.Min.Z,
    ))


def try_place(plate, brep):
    bbox = brep.GetBoundingBox(True)
    width = bbox.Max.X - bbox.Min.X
    depth = bbox.Max.Y - bbox.Min.Y
    max_x = PLATE_X_MM - MARGIN_MM
    max_y = PLATE_Y_MM - MARGIN_MM

    if plate["x"] + width <= max_x and plate["y"] + depth <= max_y:
        place_solid(brep, plate["x"], plate["y"])
        plate["solids"].append(brep)
        plate["x"] += width + SPACING_MM
        plate["row_h"] = max(plate["row_h"], depth)
        return True

    new_y = plate["y"] + plate["row_h"] + SPACING_MM
    if MARGIN_MM + width <= max_x and new_y + depth <= max_y:
        plate["x"] = MARGIN_MM
        plate["y"] = new_y
        plate["row_h"] = 0.0
        place_solid(brep, plate["x"], plate["y"])
        plate["solids"].append(brep)
        plate["x"] += width + SPACING_MM
        plate["row_h"] = max(plate["row_h"], depth)
        return True

    return False


def center_plate(plate):
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    for brep in plate["solids"]:
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
    for brep in plate["solids"]:
        brep.Transform(shift)
    return mins, maxs


def pack_into_plates(solids):
    prepared = []
    for solid in solids:
        brep = scale_to_mm(solid)
        drop_to_z_zero(brep)
        orient_for_plate(brep)
        prepared.append(brep)

    prepared.sort(key=lambda brep: max(bbox_dims(brep)[0], bbox_dims(brep)[1]), reverse=True)
    plates = []
    for brep in prepared:
        placed = False
        for plate in plates:
            if try_place(plate, brep):
                placed = True
                break
        if placed:
            continue
        plate = {"solids": [], "x": MARGIN_MM, "y": MARGIN_MM, "row_h": 0.0}
        if not try_place(plate, brep):
            place_solid(brep, MARGIN_MM, MARGIN_MM)
            plate["solids"].append(brep)
        plates.append(plate)

    for plate in plates:
        center_plate(plate)
    return plates


def mesh_brep(brep, tolerance):
    parameters = Rhino.Geometry.MeshingParameters()
    parameters.Tolerance = max(tolerance, 0.005)
    parameters.MinimumEdgeLength = 0.005
    parameters.MaximumEdgeLength = 1.0
    parameters.JaggedSeams = False
    parameters.RefineGrid = True
    parameters.SimplePlanes = False
    meshes = Rhino.Geometry.Mesh.CreateFromBrep(brep, parameters)
    clean = []
    if meshes:
        for mesh in meshes:
            if mesh:
                mesh.Normals.ComputeNormals()
                mesh.FaceNormals.ComputeFaceNormals()
                mesh.Compact()
                clean.append(mesh)
    return clean


def point_from_vertex(vertex):
    return Rhino.Geometry.Point3d(vertex.X, vertex.Y, vertex.Z)


def tri_normal(p0, p1, p2):
    normal = Rhino.Geometry.Vector3d.CrossProduct(p1 - p0, p2 - p0)
    if not normal.Unitize():
        return Rhino.Geometry.Vector3d(0, 0, 0)
    return normal


def iter_mesh_triangles(mesh):
    vertices = mesh.Vertices
    for face in mesh.Faces:
        if face.IsTriangle:
            indexes = [(face.A, face.B, face.C)]
        else:
            indexes = [(face.A, face.B, face.C), (face.A, face.C, face.D)]
        for a, b, c in indexes:
            p0 = point_from_vertex(vertices[a])
            p1 = point_from_vertex(vertices[b])
            p2 = point_from_vertex(vertices[c])
            yield tri_normal(p0, p1, p2), p0, p1, p2


def write_binary_stl(meshes, path):
    triangles = []
    for mesh in meshes:
        triangles.extend(list(iter_mesh_triangles(mesh)))
    header = ("Created by Codex from Rhino perforated panels, units are millimeters").ljust(80)[:80]
    with open(path, "wb") as handle:
        handle.write(header.encode("ascii"))
        handle.write(struct.pack("<I", len(triangles)))
        for normal, p0, p1, p2 in triangles:
            values = [
                normal.X, normal.Y, normal.Z,
                p0.X, p0.Y, p0.Z,
                p1.X, p1.Y, p1.Z,
                p2.X, p2.Y, p2.Z,
            ]
            handle.write(struct.pack("<12fH", *(values + [0])))
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


def plate_extents_and_volume(solids):
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    volume = 0.0
    for brep in solids:
        bbox = brep.GetBoundingBox(True)
        mins[0] = min(mins[0], bbox.Min.X)
        mins[1] = min(mins[1], bbox.Min.Y)
        mins[2] = min(mins[2], bbox.Min.Z)
        maxs[0] = max(maxs[0], bbox.Max.X)
        maxs[1] = max(maxs[1], bbox.Max.Y)
        maxs[2] = max(maxs[2], bbox.Max.Z)
        amp = Rhino.Geometry.VolumeMassProperties.Compute(brep)
        if amp:
            volume += amp.Volume
    return (maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2]), volume


try:
    open(SUMMARY_PATH, "w").close()
    source = source_solids()
    if not source:
        raise Exception("No valid closed Brep solids found on {}".format(SOURCE_LAYER))

    unit_scale = Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)
    source_mm = [scale_to_mm(brep) for brep in source]
    source_reference = os.path.join(OUT_DIR, "{}_SOURCE_REFERENCE_ORIGINAL_{}.3dm".format(DOC_STEM, STAMP))
    write_3dm(source_reference, source_mm, "source_reference_perforated_panel")

    source_thicknesses = sorted(bbox_dims(brep)[2] * unit_scale for brep in source)
    write_text(SUMMARY_PATH, "source={}".format(DOC_PATH))
    write_text(SUMMARY_PATH, "source_reference={}".format(source_reference))
    write_text(SUMMARY_PATH, "units={}".format(sc.doc.ModelUnitSystem))
    write_text(SUMMARY_PATH, "source_layer={}".format(SOURCE_LAYER))
    write_text(SUMMARY_PATH, "source_count={}".format(len(source)))
    write_text(SUMMARY_PATH, "source_thickness_mm={:.9f}".format(source_thicknesses[len(source_thicknesses) // 2]))

    for label, target_mm, note in TARGETS:
        target_model_units = target_mm / unit_scale
        adjusted = []
        for solid in source:
            brep = solid.DuplicateBrep()
            scale_z_to_thickness(brep, target_model_units)
            if brep.IsValid and brep.IsSolid:
                adjusted.append(brep)
        if not adjusted:
            raise Exception("No valid adjusted solids for {}".format(label))

        plates = pack_into_plates(adjusted)
        write_text(SUMMARY_PATH, "target={}|{:.9f}|{}".format(label, target_mm, note))
        write_text(SUMMARY_PATH, "plates={}".format(len(plates)))

        for plate_index, plate in enumerate(plates, start=1):
            meshes = []
            for solid in plate["solids"]:
                meshes.extend(mesh_brep(solid, 0.005))

            final_stl = os.path.join(OUT_DIR, "{}_PERF_PANELS_{}_plate{}_FINAL.stl".format(DOC_STEM, label, plate_index))
            final_3dm = os.path.join(OUT_DIR, "{}_PERF_PANELS_{}_plate{}_PRINT_READY.3dm".format(DOC_STEM, label, plate_index))
            log_path = os.path.join(OUT_DIR, "{}_perf_panels_{}_{}_log.txt".format(DOC_STEM, label, STAMP))

            triangles = write_binary_stl(meshes, final_stl)
            write_3dm(final_3dm, plate["solids"], "perf_panel_{}_plate{}".format(label, plate_index))
            extents, volume = plate_extents_and_volume(plate["solids"])

            with open(log_path, "a") as log:
                log.write("Source: {}\\n".format(DOC_PATH))
                log.write("Target: {} {:.9f} mm ({})\\n".format(label, target_mm, note))
                log.write("Plate {} parts: {}\\n".format(plate_index, len(plate["solids"])))
                log.write("Plate {} extents: {:.6f}, {:.6f}, {:.6f}\\n".format(plate_index, extents[0], extents[1], extents[2]))
                log.write("Plate {} volume: {:.6f}\\n".format(plate_index, volume))
                log.write("Plate {} triangles: {}\\n".format(plate_index, triangles))
                log.write("Plate {} STL: {}\\n".format(plate_index, final_stl))
                log.write("Plate {} 3DM: {}\\n".format(plate_index, final_3dm))

            write_text(SUMMARY_PATH, "final={}|{}|{}|{}|{:.6f},{:.6f},{:.6f}|{:.6f}|{}".format(
                label,
                plate_index,
                final_stl,
                final_3dm,
                extents[0], extents[1], extents[2],
                volume,
                len(plate["solids"]),
            ))
    print("PERF_PANEL_EXPORT_DONE {}".format(SUMMARY_PATH))
except Exception:
    write_text(SUMMARY_PATH, traceback.format_exc())
    print(traceback.format_exc())
