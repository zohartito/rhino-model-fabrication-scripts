import os
import struct
import time
import traceback

import Rhino
import scriptcontext as sc

SOURCE_LAYER = "15_CU_PUNCH_RETURNS_SOUTH_BAY_ALIGNED_V44"
WALL_MM = 0.66
MARGIN_MM = 10.0
SPACING_MM = 5.0
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_DIR = "path/to/return-output"
BASE = "bottom south band return6_printable_0p66mm_{}".format(STAMP)
OUT_STL = os.path.join(OUT_DIR, BASE + ".stl")
OUT_3DM = os.path.join(OUT_DIR, BASE + ".3dm")
LOG = os.path.join(OUT_DIR, BASE + "_log.txt")
OUTPUT_LAYER = "CODEx_PRINTABLE_0p66mm_{}".format(STAMP)


def log(message):
    with open(LOG, "a") as f:
        f.write(str(message) + "\n")
    print(message)


def ensure_layer(name):
    index = sc.doc.Layers.FindByFullPath(name, True)
    if index >= 0:
        return index
    layer = Rhino.DocObjects.Layer()
    layer.Name = name
    layer.Color = System.Drawing.Color.FromArgb(0, 150, 255)
    return sc.doc.Layers.Add(layer)


def get_source_breps():
    breps = []
    for obj in sc.doc.Objects:
        layer = sc.doc.Layers[obj.Attributes.LayerIndex]
        if not layer or layer.Name != SOURCE_LAYER:
            continue
        brep = obj.Geometry if isinstance(obj.Geometry, Rhino.Geometry.Brep) else None
        if brep is not None:
            breps.append(brep.DuplicateBrep())
    return breps


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
            if part.IsValid and part.IsSolid:
                good.append(part)
        if good:
            return good, dist
    return last, attempts[-1]


def drop_to_z_zero(brep):
    bbox = brep.GetBoundingBox(True)
    xform = Rhino.Geometry.Transform.Translation(0, 0, -bbox.Min.Z)
    brep.Transform(xform)
    return -bbox.Min.Z


def pack_solids_for_plate(solids):
    packed = sorted(solids, key=lambda b: b.GetBoundingBox(True).Max.Z, reverse=True)
    y_cursor = MARGIN_MM
    for i, brep in enumerate(packed):
        bbox = brep.GetBoundingBox(True)
        xform = Rhino.Geometry.Transform.Translation(
            MARGIN_MM - bbox.Min.X,
            y_cursor - bbox.Min.Y,
            -bbox.Min.Z,
        )
        brep.Transform(xform)
        new_bbox = brep.GetBoundingBox(True)
        log("Packed solid {} bounds: min=({:.3f}, {:.3f}, {:.3f}) max=({:.3f}, {:.3f}, {:.3f})".format(
            i + 1,
            new_bbox.Min.X, new_bbox.Min.Y, new_bbox.Min.Z,
            new_bbox.Max.X, new_bbox.Max.Y, new_bbox.Max.Z,
        ))
        y_cursor = new_bbox.Max.Y + SPACING_MM
    log("Packed layout extents: X max <= {:.3f}, Y max <= {:.3f}".format(
        max(b.GetBoundingBox(True).Max.X for b in packed) if packed else 0,
        max(b.GetBoundingBox(True).Max.Y for b in packed) if packed else 0,
    ))
    return packed


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


def write_3dm(solids, path):
    file3dm = Rhino.FileIO.File3dm()
    file3dm.Settings.ModelUnitSystem = sc.doc.ModelUnitSystem
    file3dm.Settings.ModelAbsoluteTolerance = sc.doc.ModelAbsoluteTolerance
    attrs = Rhino.DocObjects.ObjectAttributes()
    attrs.Name = "printable_0p66mm_solid"
    for brep in solids:
        file3dm.Objects.AddBrep(brep, attrs)
    file3dm.Write(path, 8)


try:
    import System.Drawing

    open(LOG, "w").close()
    tol = sc.doc.ModelAbsoluteTolerance
    log("Making printable returns")
    log("Source layer: {}".format(SOURCE_LAYER))
    log("Wall thickness: {} mm".format(WALL_MM))
    log("Plate packing margin/spacing: {} mm / {} mm".format(MARGIN_MM, SPACING_MM))
    log("Document units: {}".format(sc.doc.ModelUnitSystem))
    log("Tolerance: {}".format(tol))

    source = get_source_breps()
    log("Source breps found: {}".format(len(source)))
    if len(source) != 24:
        log("WARNING: expected 24 source surfaces, found {}".format(len(source)))

    joined = Rhino.Geometry.Brep.JoinBreps(source, tol)
    joined = list(joined) if joined else []
    log("Joined return shells: {}".format(len(joined)))

    solids = []
    for i, shell in enumerate(joined):
        log("Return {} input: faces={}, edges={}, solid={}, valid={}".format(
            i + 1, shell.Faces.Count, shell.Edges.Count, shell.IsSolid, shell.IsValid
        ))
        result, used_dist = offset_to_solid(shell, WALL_MM, tol)
        log("Return {} offset used distance: {}".format(i + 1, used_dist))
        log("Return {} offset parts: {}".format(i + 1, len(result)))
        for j, solid in enumerate(result):
            if not solid:
                continue
            shift = drop_to_z_zero(solid)
            log("Return {}.{} output: faces={}, edges={}, solid={}, valid={}, z_shift={}".format(
                i + 1, j + 1, solid.Faces.Count, solid.Edges.Count, solid.IsSolid, solid.IsValid, shift
            ))
            solids.append(solid)

    valid_solids = [b for b in solids if b and b.IsValid and b.IsSolid]
    log("Valid closed solids: {} / {}".format(len(valid_solids), len(solids)))
    if len(valid_solids) != 6:
        log("WARNING: expected 6 valid closed solids.")
    valid_solids = pack_solids_for_plate(valid_solids)

    layer_index = ensure_layer(OUTPUT_LAYER)
    attrs = Rhino.DocObjects.ObjectAttributes()
    attrs.LayerIndex = layer_index
    attrs.Name = "printable_0p66mm_solid"
    added = []
    for solid in valid_solids:
        added.append(sc.doc.Objects.AddBrep(solid, attrs))
    log("Added solids to Rhino layer '{}': {}".format(OUTPUT_LAYER, len(added)))

    all_meshes = []
    for solid in valid_solids:
        all_meshes.extend(mesh_brep(solid, tol))
    log("Mesh parts generated: {}".format(len(all_meshes)))
    log("Closed mesh parts: {} / {}".format(
        sum(1 for mesh in all_meshes if mesh.IsClosed), len(all_meshes)
    ))

    tri_count = write_binary_stl(all_meshes, OUT_STL)
    log("Wrote STL triangles: {}".format(tri_count))
    log("STL: {}".format(OUT_STL))

    write_3dm(valid_solids, OUT_3DM)
    log("3DM: {}".format(OUT_3DM))

    sc.doc.Views.Redraw()
    log("DONE")
except Exception:
    log(traceback.format_exc())
