import os
import re
import struct
import time
import traceback

import Rhino
import scriptcontext as sc


DEFAULT_MODEL_PATH = "path/to/unsaved-model.3dm"
SUMMARY_DIR = "path/to/logs"

SOURCE_LAYER = "14_CU_CORR_PERF_SCREEN"
TARGETS = [
    ("0p01in", 0.254, "literal 0.01 in minimum rib/flange thickness"),
    ("MATCH_SOUTH_STL", 0.5705078125, "matches measured South STL panel thickness as minimum rib/flange thickness"),
]
PLATE_X_MM = 250.0
PLATE_Y_MM = 250.0
MARGIN_MM = 5.0
SPACING_MM = 5.0


def safe_stem(name):
    name = os.path.splitext(name)[0].strip()
    name = re.sub(r"[^A-Za-z0-9_. -]+", "_", name).strip()
    return name or "rhino_corr_perf_panels"


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


def bbox_values(brep):
    bbox = brep.GetBoundingBox(True)
    return [bbox.Min.X, bbox.Min.Y, bbox.Min.Z], [bbox.Max.X, bbox.Max.Y, bbox.Max.Z]


def bbox_dims(brep):
    mn, mx = bbox_values(brep)
    return (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])


def source_solids():
    solids = []
    for rhino_object in sc.doc.Objects:
        if object_layer_name(rhino_object) != SOURCE_LAYER:
            continue
        brep = rhino_object.Geometry if isinstance(rhino_object.Geometry, Rhino.Geometry.Brep) else None
        if brep and brep.IsSolid and brep.IsValid:
            solids.append(brep.DuplicateBrep())
    return solids


def boxes_touch(a, b, tol):
    amin, amax = bbox_values(a)
    bmin, bmax = bbox_values(b)
    return not (
        amax[0] < bmin[0] - tol or bmax[0] < amin[0] - tol or
        amax[1] < bmin[1] - tol or bmax[1] < amin[1] - tol or
        amax[2] < bmin[2] - tol or bmax[2] < amin[2] - tol
    )


def connected_components(group, tol):
    parent = list(range(len(group)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a, b):
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for a in range(len(group)):
        for b in range(a + 1, len(group)):
            if boxes_touch(group[a], group[b], tol):
                union(a, b)

    components = {}
    for index, brep in enumerate(group):
        components.setdefault(find(index), []).append(brep)
    return list(components.values())


def group_source_solids(solids):
    y_groups = {}
    for brep in solids:
        mn, mx = bbox_values(brep)
        key = (round(mn[1], 6), round(mx[1], 6))
        y_groups.setdefault(key, []).append(brep)

    connected = []
    for key in sorted(y_groups):
        connected.extend(connected_components(y_groups[key], 0.0001))
    return [y_groups[key] for key in sorted(y_groups)], connected


def group_bounds(group):
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    for brep in group:
        mn, mx = bbox_values(brep)
        for axis in range(3):
            mins[axis] = min(mins[axis], mn[axis])
            maxs[axis] = max(maxs[axis], mx[axis])
    return mins, maxs


def axis_scale_transform(axis, factor, anchor):
    xform = Rhino.Geometry.Transform.Identity
    if axis == 0:
        xform.M00 = factor
        xform.M03 = anchor * (1.0 - factor)
    elif axis == 2:
        xform.M22 = factor
        xform.M23 = anchor * (1.0 - factor)
    return xform


def thicken_group(group, target_model_units):
    thickened = [brep.DuplicateBrep() for brep in group]
    gmin, gmax = group_bounds(thickened)
    tol = max(sc.doc.ModelAbsoluteTolerance, 0.001)
    for brep in thickened:
        for axis in (0, 2):
            mn, mx = bbox_values(brep)
            current = mx[axis] - mn[axis]
            if current <= 1e-9 or current >= target_model_units:
                continue
            if abs(mn[axis] - gmin[axis]) <= tol:
                anchor = mn[axis]
            elif abs(mx[axis] - gmax[axis]) <= tol:
                anchor = mx[axis]
            else:
                anchor = 0.5 * (mn[axis] + mx[axis])
            brep.Transform(axis_scale_transform(axis, target_model_units / current, anchor))
    return thickened


def boolean_union_group(group):
    unioned = Rhino.Geometry.Brep.CreateBooleanUnion(group, sc.doc.ModelAbsoluteTolerance)
    if unioned and len(unioned) > 0:
        clean = [brep for brep in unioned if brep and brep.IsValid and brep.IsSolid]
        if clean:
            return clean
    return group


def scale_to_mm_group(group):
    scale = Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)
    duplicates = [brep.DuplicateBrep() for brep in group]
    if abs(scale - 1.0) > 1e-9:
        xform = Rhino.Geometry.Transform.Scale(Rhino.Geometry.Point3d.Origin, scale)
        for brep in duplicates:
            brep.Transform(xform)
    return duplicates


def translate_group(group, dx, dy, dz):
    xform = Rhino.Geometry.Transform.Translation(dx, dy, dz)
    for brep in group:
        brep.Transform(xform)


def group_dims(group):
    mn, mx = group_bounds(group)
    return mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]


def place_group(group, target_x, target_y):
    mn, _ = group_bounds(group)
    translate_group(group, target_x - mn[0], target_y - mn[1], -mn[2])


def try_place(plate, group):
    width, depth, _ = group_dims(group)
    max_x = PLATE_X_MM - MARGIN_MM
    max_y = PLATE_Y_MM - MARGIN_MM

    if plate["x"] + width <= max_x and plate["y"] + depth <= max_y:
        place_group(group, plate["x"], plate["y"])
        plate["groups"].append(group)
        plate["x"] += width + SPACING_MM
        plate["row_h"] = max(plate["row_h"], depth)
        return True

    new_y = plate["y"] + plate["row_h"] + SPACING_MM
    if MARGIN_MM + width <= max_x and new_y + depth <= max_y:
        plate["x"] = MARGIN_MM
        plate["y"] = new_y
        plate["row_h"] = 0.0
        place_group(group, plate["x"], plate["y"])
        plate["groups"].append(group)
        plate["x"] += width + SPACING_MM
        plate["row_h"] = max(plate["row_h"], depth)
        return True

    return False


def center_plate(plate):
    all_breps = [brep for group in plate["groups"] for brep in group]
    mins, maxs = group_bounds(all_breps)
    translate_group(
        all_breps,
        -0.5 * (mins[0] + maxs[0]),
        -0.5 * (mins[1] + maxs[1]),
        -mins[2],
    )


def pack_groups(groups):
    prepared = []
    for group in groups:
        mm_group = scale_to_mm_group(group)
        mn, _ = group_bounds(mm_group)
        translate_group(mm_group, 0, 0, -mn[2])
        prepared.append(mm_group)

    prepared.sort(key=lambda group: max(group_dims(group)[0], group_dims(group)[1]), reverse=True)
    plates = []
    for group in prepared:
        placed = False
        for plate in plates:
            if try_place(plate, group):
                placed = True
                break
        if placed:
            continue
        plate = {"groups": [], "x": MARGIN_MM, "y": MARGIN_MM, "row_h": 0.0}
        if not try_place(plate, group):
            place_group(group, MARGIN_MM, MARGIN_MM)
            plate["groups"].append(group)
        plates.append(plate)

    for plate in plates:
        center_plate(plate)
    return plates


def mesh_brep(brep, tolerance):
    parameters = Rhino.Geometry.MeshingParameters()
    parameters.Tolerance = max(tolerance, 0.01)
    parameters.MinimumEdgeLength = 0.02
    parameters.MaximumEdgeLength = 1.5
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
    header = ("Created by Codex from Rhino corr perforated panels, units are millimeters").ljust(80)[:80]
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


def write_3dm(path, groups, object_name):
    file3dm = Rhino.FileIO.File3dm()
    file3dm.Settings.ModelUnitSystem = Rhino.UnitSystem.Millimeters
    file3dm.Settings.ModelAbsoluteTolerance = 0.01
    for group_index, group in enumerate(groups, start=1):
        attrs = Rhino.DocObjects.ObjectAttributes()
        attrs.Name = "{}_group{:02d}".format(object_name, group_index)
        for brep in group:
            file3dm.Objects.AddBrep(brep, attrs)
    file3dm.Write(path, 8)


def plate_extents_and_volume(groups):
    all_breps = [brep for group in groups for brep in group]
    mins, maxs = group_bounds(all_breps)
    volume = 0.0
    for brep in all_breps:
        amp = Rhino.Geometry.VolumeMassProperties.Compute(brep)
        if amp:
            volume += amp.Volume
    return (maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2]), volume


try:
    open(SUMMARY_PATH, "w").close()
    source = source_solids()
    if not source:
        raise Exception("No valid closed Brep solids found on {}".format(SOURCE_LAYER))

    y_groups, connected_groups = group_source_solids(source)
    unit_scale = Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)
    source_mm = [scale_to_mm_group([brep])[0] for brep in source]
    source_reference = os.path.join(OUT_DIR, "{}_SOURCE_REFERENCE_ORIGINAL_{}.3dm".format(DOC_STEM, STAMP))
    write_3dm(source_reference, [source_mm], "source_reference_corr_perf")

    thin_dims = []
    for brep in source:
        dims = bbox_dims(brep)
        thin_dims.extend([dims[0] * unit_scale, dims[2] * unit_scale])
    thin_dims = sorted(value for value in thin_dims if value < 1.0)

    write_text(SUMMARY_PATH, "source={}".format(DOC_PATH))
    write_text(SUMMARY_PATH, "source_reference={}".format(source_reference))
    write_text(SUMMARY_PATH, "units={}".format(sc.doc.ModelUnitSystem))
    write_text(SUMMARY_PATH, "source_layer={}".format(SOURCE_LAYER))
    write_text(SUMMARY_PATH, "source_count={}".format(len(source)))
    write_text(SUMMARY_PATH, "y_span_count={}".format(len(y_groups)))
    write_text(SUMMARY_PATH, "connected_group_count={}".format(len(connected_groups)))
    write_text(SUMMARY_PATH, "boxes_per_y_span={}".format(sorted(set(len(group) for group in y_groups))))
    write_text(SUMMARY_PATH, "boxes_per_connected_group={}".format(sorted(set(len(group) for group in connected_groups))))
    write_text(SUMMARY_PATH, "thin_dimension_mm={:.9f}".format(thin_dims[len(thin_dims) // 2] if thin_dims else 0))

    for label, target_mm, note in TARGETS:
        target_model_units = target_mm / unit_scale
        adjusted_groups = [boolean_union_group(thicken_group(group, target_model_units)) for group in connected_groups]
        plates = pack_groups(adjusted_groups)
        write_text(SUMMARY_PATH, "target={}|{:.9f}|{}".format(label, target_mm, note))
        write_text(SUMMARY_PATH, "plates={}".format(len(plates)))

        log_path = os.path.join(OUT_DIR, "{}_corr_perf_panels_{}_{}_log.txt".format(DOC_STEM, label, STAMP))
        for plate_index, plate in enumerate(plates, start=1):
            meshes = []
            for group in plate["groups"]:
                for brep in group:
                    meshes.extend(mesh_brep(brep, 0.01))

            final_stl = os.path.join(OUT_DIR, "{}_CORR_PERF_PANELS_{}_plate{}_FINAL.stl".format(DOC_STEM, label, plate_index))
            final_3dm = os.path.join(OUT_DIR, "{}_CORR_PERF_PANELS_{}_plate{}_PRINT_READY.3dm".format(DOC_STEM, label, plate_index))
            triangles = write_binary_stl(meshes, final_stl)
            write_3dm(final_3dm, plate["groups"], "corr_perf_panel_{}_plate{}".format(label, plate_index))
            extents, volume = plate_extents_and_volume(plate["groups"])

            with open(log_path, "a") as log:
                log.write("Source: {}\\n".format(DOC_PATH))
                log.write("Target: {} {:.9f} mm ({})\\n".format(label, target_mm, note))
                log.write("Plate {} connected groups: {}\\n".format(plate_index, len(plate["groups"])))
                log.write("Plate {} Brep solids: {}\\n".format(plate_index, sum(len(group) for group in plate["groups"])))
                log.write("Plate {} extents: {:.6f}, {:.6f}, {:.6f}\\n".format(plate_index, extents[0], extents[1], extents[2]))
                log.write("Plate {} volume: {:.6f}\\n".format(plate_index, volume))
                log.write("Plate {} triangles: {}\\n".format(plate_index, triangles))
                log.write("Plate {} STL: {}\\n".format(plate_index, final_stl))
                log.write("Plate {} 3DM: {}\\n".format(plate_index, final_3dm))

            write_text(SUMMARY_PATH, "final={}|{}|{}|{}|{:.6f},{:.6f},{:.6f}|{:.6f}|{}|{}".format(
                label,
                plate_index,
                final_stl,
                final_3dm,
                extents[0], extents[1], extents[2],
                volume,
                len(plate["groups"]),
                sum(len(group) for group in plate["groups"]),
            ))
    print("CORR_PERF_PANEL_EXPORT_DONE {}".format(SUMMARY_PATH))
except Exception:
    write_text(SUMMARY_PATH, traceback.format_exc())
    print(traceback.format_exc())
