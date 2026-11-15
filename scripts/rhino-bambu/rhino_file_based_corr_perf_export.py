import os
import re
import struct
import time
import traceback

import Rhino


SOURCE = "path/to/corr-perf-band.3dm"
SOURCE_LAYER = "14_CU_CORR_PERF_SCREEN"
OUT_DIR = "path/to/corr-perf-output"
TARGETS = [
    ("0p01in", 0.254, "literal 0.01 in minimum rib/flange thickness"),
    ("MATCH_SOUTH_STL", 0.5705078125, "matches measured South STL panel thickness as minimum rib/flange thickness"),
]
PLATE_X_MM = 250.0
PLATE_Y_MM = 250.0
MARGIN_MM = 5.0
SPACING_MM = 5.0
TOL = 0.01
STAMP = time.strftime("%Y%m%d_%H%M%S")
SUMMARY_PATH = os.path.join(OUT_DIR, "middle_corr_perf_west_band_summary.txt")


def safe_stem(path):
    stem = os.path.splitext(os.path.basename(path))[0].strip()
    return re.sub(r"[^A-Za-z0-9_. -]+", "_", stem).strip()


DOC_STEM = safe_stem(SOURCE)


def log(message):
    with open(SUMMARY_PATH, "a") as handle:
        handle.write(str(message) + "\n")


def bbox_values(brep):
    bbox = brep.GetBoundingBox(True)
    return [bbox.Min.X, bbox.Min.Y, bbox.Min.Z], [bbox.Max.X, bbox.Max.Y, bbox.Max.Z]


def group_bounds(group):
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    for brep in group:
        mn, mx = bbox_values(brep)
        for axis in range(3):
            mins[axis] = min(mins[axis], mn[axis])
            maxs[axis] = max(maxs[axis], mx[axis])
    return mins, maxs


def touch(a, b, tol=0.0001):
    amin, amax = bbox_values(a)
    bmin, bmax = bbox_values(b)
    return not (
        amax[0] < bmin[0] - tol or bmax[0] < amin[0] - tol or
        amax[1] < bmin[1] - tol or bmax[1] < amin[1] - tol or
        amax[2] < bmin[2] - tol or bmax[2] < amin[2] - tol
    )


def connected_components(group):
    parent = list(range(len(group)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    for a in range(len(group)):
        for b in range(a + 1, len(group)):
            if touch(group[a], group[b]):
                union(a, b)

    result = {}
    for i, brep in enumerate(group):
        result.setdefault(find(i), []).append(brep)
    return list(result.values())


def layer_breps(model):
    layer_index = None
    for i, layer in enumerate(model.AllLayers):
        if layer.Name == SOURCE_LAYER:
            layer_index = i
            break
    if layer_index is None:
        raise Exception("Layer not found: {}".format(SOURCE_LAYER))

    breps = []
    for obj in model.Objects:
        if obj.Attributes.LayerIndex != layer_index:
            continue
        geo = obj.Geometry
        if isinstance(geo, Rhino.Geometry.Brep) and geo.IsValid and geo.IsSolid:
            breps.append(geo.DuplicateBrep())
    return breps


def source_groups(breps):
    y_groups = {}
    for brep in breps:
        mn, mx = bbox_values(brep)
        y_groups.setdefault((round(mn[1], 6), round(mx[1], 6)), []).append(brep)
    connected = []
    for key in sorted(y_groups):
        connected.extend(connected_components(y_groups[key]))
    return [y_groups[key] for key in sorted(y_groups)], connected


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
    for brep in thickened:
        for axis in (0, 2):
            mn, mx = bbox_values(brep)
            current = mx[axis] - mn[axis]
            if current <= 1e-9 or current >= target_model_units:
                continue
            if abs(mn[axis] - gmin[axis]) <= TOL:
                anchor = mn[axis]
            elif abs(mx[axis] - gmax[axis]) <= TOL:
                anchor = mx[axis]
            else:
                anchor = 0.5 * (mn[axis] + mx[axis])
            brep.Transform(axis_scale_transform(axis, target_model_units / current, anchor))
    return thickened


def boolean_union_group(group):
    unioned = Rhino.Geometry.Brep.CreateBooleanUnion(group, TOL)
    if unioned:
        clean = [brep for brep in unioned if brep and brep.IsValid and brep.IsSolid]
        if clean:
            return clean
    return group


def duplicate_group(group):
    return [brep.DuplicateBrep() for brep in group]


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
    mn, mx = group_bounds(all_breps)
    translate_group(all_breps, -0.5 * (mn[0] + mx[0]), -0.5 * (mn[1] + mx[1]), -mn[2])


def pack_groups(groups):
    prepared = [duplicate_group(group) for group in groups]
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
            raise Exception("Group does not fit plate: dims={}".format(group_dims(group)))
        plates.append(plate)
    for plate in plates:
        center_plate(plate)
    return plates


def mesh_brep(brep):
    mp = Rhino.Geometry.MeshingParameters()
    mp.Tolerance = 0.02
    mp.MinimumEdgeLength = 0.02
    mp.MaximumEdgeLength = 2.0
    mp.JaggedSeams = False
    mp.RefineGrid = True
    mp.SimplePlanes = False
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
        indexes = [(face.A, face.B, face.C)] if face.IsTriangle else [(face.A, face.B, face.C), (face.A, face.C, face.D)]
        for a, b, c in indexes:
            p0 = point_from_vertex(vertices[a])
            p1 = point_from_vertex(vertices[b])
            p2 = point_from_vertex(vertices[c])
            yield tri_normal(p0, p1, p2), p0, p1, p2


def write_binary_stl(meshes, path):
    triangles = []
    for mesh in meshes:
        triangles.extend(list(iter_mesh_triangles(mesh)))
    header = "Created by Codex file-based middle corr perf, units millimeters".ljust(80)[:80]
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
    file3dm.Settings.ModelAbsoluteTolerance = TOL
    for group_index, group in enumerate(groups, start=1):
        attrs = Rhino.DocObjects.ObjectAttributes()
        attrs.Name = "{}_group{:02d}".format(object_name, group_index)
        for brep in group:
            file3dm.Objects.AddBrep(brep, attrs)
    file3dm.Write(path, 8)


def plate_extents_volume(groups):
    all_breps = [brep for group in groups for brep in group]
    mn, mx = group_bounds(all_breps)
    volume = 0.0
    for brep in all_breps:
        amp = Rhino.Geometry.VolumeMassProperties.Compute(brep)
        if amp:
            volume += amp.Volume
    return [mx[i] - mn[i] for i in range(3)], volume


try:
    os.makedirs(OUT_DIR, exist_ok=True)
    open(SUMMARY_PATH, "w").close()
    model = Rhino.FileIO.File3dm.Read(SOURCE)
    log("source={}".format(SOURCE))
    log("output_dir={}".format(OUT_DIR))
    log("file_units={}".format(model.Settings.ModelUnitSystem))
    unit_scale = Rhino.RhinoMath.UnitScale(model.Settings.ModelUnitSystem, Rhino.UnitSystem.Millimeters)
    breps = layer_breps(model)
    y_groups, connected = source_groups(breps)
    log("source_layer={}".format(SOURCE_LAYER))
    log("source_count={}".format(len(breps)))
    log("y_span_count={}".format(len(y_groups)))
    log("connected_group_count={}".format(len(connected)))
    log("boxes_per_y_span={}".format(sorted(set(len(group) for group in y_groups))))
    log("boxes_per_connected_group={}".format(sorted(set(len(group) for group in connected))))

    source_reference = os.path.join(OUT_DIR, "{}_SOURCE_REFERENCE_ORIGINAL_{}.3dm".format(DOC_STEM, STAMP))
    write_3dm(source_reference, [breps], "source_reference_middle_corr_perf")
    log("source_reference={}".format(source_reference))

    for label, target_mm, note in TARGETS:
        target_model_units = target_mm / unit_scale
        log("target={}|{:.9f}|{}".format(label, target_mm, note))
        adjusted = [boolean_union_group(thicken_group(group, target_model_units)) for group in connected]
        log("post_union_group_count={}".format(len(adjusted)))
        log("post_union_breps_per_group={}".format(sorted(set(len(group) for group in adjusted))))
        plates = pack_groups(adjusted)
        log("plates={}".format(len(plates)))
        for plate_index, plate in enumerate(plates, start=1):
            meshes = []
            for group in plate["groups"]:
                for brep in group:
                    meshes.extend(mesh_brep(brep))
            final_stl = os.path.join(OUT_DIR, "{}_CORR_PERF_PANELS_{}_plate{}_FINAL.stl".format(DOC_STEM, label, plate_index))
            final_3dm = os.path.join(OUT_DIR, "{}_CORR_PERF_PANELS_{}_plate{}_PRINT_READY.3dm".format(DOC_STEM, label, plate_index))
            triangles = write_binary_stl(meshes, final_stl)
            write_3dm(final_3dm, plate["groups"], "corr_perf_panel_{}_plate{}".format(label, plate_index))
            extents, volume = plate_extents_volume(plate["groups"])
            log("final={}|{}|{}|{}|{:.6f},{:.6f},{:.6f}|{:.6f}|{}|{}|{}".format(
                label,
                plate_index,
                final_stl,
                final_3dm,
                extents[0], extents[1], extents[2],
                volume,
                len(plate["groups"]),
                sum(len(group) for group in plate["groups"]),
                triangles,
            ))
except Exception:
    log(traceback.format_exc())
