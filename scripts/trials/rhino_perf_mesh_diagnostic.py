import os
import traceback

import Rhino
import scriptcontext as sc


LOG = "path/to/logs/top_flat_perf_mesh_diagnostic.txt"
SOURCE_LAYER = "13_CU_FLAT_PERF_TRANSLUCENT"


def log(message):
    with open(LOG, "a") as handle:
        handle.write(str(message) + "\n")


try:
    open(LOG, "w").close()
    solids = []
    for rhino_object in sc.doc.Objects:
        layer = sc.doc.Layers[rhino_object.Attributes.LayerIndex]
        if layer.Name != SOURCE_LAYER:
            continue
        brep = rhino_object.Geometry if isinstance(rhino_object.Geometry, Rhino.Geometry.Brep) else None
        if brep and brep.IsSolid and brep.IsValid:
            solids.append(brep.DuplicateBrep())
    log("solids={}".format(len(solids)))
    mp = Rhino.Geometry.MeshingParameters()
    mp.Tolerance = 0.01
    mp.MinimumEdgeLength = 0.02
    mp.MaximumEdgeLength = 2.0
    mp.RefineGrid = True
    mp.SimplePlanes = False
    for index, brep in enumerate(solids[:5], start=1):
        log("before mesh {}".format(index))
        meshes = Rhino.Geometry.Mesh.CreateFromBrep(brep, mp)
        log("after mesh {} count={}".format(index, len(meshes) if meshes else 0))
        if meshes:
            log("faces {}".format([m.Faces.Count for m in meshes if m]))
    log("done")
except BaseException:
    log(traceback.format_exc())
