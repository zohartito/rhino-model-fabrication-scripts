import os
import traceback

import Rhino


SOURCE = "path/to/corr-perf-band.3dm"
LAYER = "14_CU_CORR_PERF_SCREEN"
LOG = "path/to/logs/corr_perf_probe.txt"


def log(message):
    with open(LOG, "a") as handle:
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


def components(group):
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


try:
    open(LOG, "w").close()
    model = Rhino.FileIO.File3dm.Read(SOURCE)
    log("file_units={}".format(model.Settings.ModelUnitSystem))
    layer_names = [layer.Name for layer in model.AllLayers]
    layer_index = layer_names.index(LAYER)
    breps = []
    for obj in model.Objects:
        if obj.Attributes.LayerIndex != layer_index:
            continue
        geo = obj.Geometry
        if isinstance(geo, Rhino.Geometry.Brep) and geo.IsValid and geo.IsSolid:
            breps.append(geo.DuplicateBrep())
    log("breps={}".format(len(breps)))
    y_groups = {}
    for brep in breps:
        mn, mx = bbox_values(brep)
        y_groups.setdefault((round(mn[1], 6), round(mx[1], 6)), []).append(brep)
    log("y_groups={}".format(len(y_groups)))
    first = y_groups[sorted(y_groups.keys())[0]]
    comps = components(first)
    log("first_y_group_count={} components={}".format(len(first), len(comps)))
    comp = comps[0]
    mn, mx = group_bounds(comp)
    log("component_before count={} dims={}".format(len(comp), [mx[i] - mn[i] for i in range(3)]))
    unioned = Rhino.Geometry.Brep.CreateBooleanUnion(comp, 0.01)
    log("union_result_count={}".format(len(unioned) if unioned else 0))
    if unioned:
        clean = [u for u in unioned if u and u.IsValid and u.IsSolid]
        log("union_clean_count={}".format(len(clean)))
        mn, mx = group_bounds(clean)
        log("union_dims={}".format([mx[i] - mn[i] for i in range(3)]))
        mp = Rhino.Geometry.MeshingParameters()
        mp.Tolerance = 0.02
        mp.MinimumEdgeLength = 0.02
        mp.MaximumEdgeLength = 2.0
        mp.SimplePlanes = False
        mesh_count = 0
        face_count = 0
        for u in clean:
            meshes = Rhino.Geometry.Mesh.CreateFromBrep(u, mp)
            if meshes:
                mesh_count += len(meshes)
                face_count += sum(m.Faces.Count for m in meshes if m)
        log("mesh_count={} faces={}".format(mesh_count, face_count))
except Exception:
    log(traceback.format_exc())
