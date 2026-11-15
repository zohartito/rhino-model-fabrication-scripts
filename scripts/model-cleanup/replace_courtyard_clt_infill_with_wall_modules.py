import json
import traceback

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc


COURT_LAYER = "03bc_CLT_BACKUP_COURT"
OLD_NAME_PREFIX = "codex_bottom_clt_infill_"
NEW_NAME_PREFIX = "codex_bottom_clt_court_wall_module_"
GROUP_NAME = "CODEX_BOTTOM_CLT_COURT_WALL_MODULES"
LOG_PATH = "path/to/logs/replace_courtyard_clt_infill.json"

SOURCE_MIN_Z = 14.503
SOURCE_MAX_Z = 18.0
SOURCE_TOL = 0.02

TARGET_MIN_Z = 10.0
TARGET_MAX_Z = 12.003


def log(message):
    print(message)


def bbox(obj):
    return obj.Geometry.GetBoundingBox(True)


def is_court_infill_to_delete(obj):
    name = obj.Attributes.Name or ""
    if name.startswith(NEW_NAME_PREFIX):
        return True
    if not name.startswith(OLD_NAME_PREFIX):
        return False
    layer = sc.doc.Layers[obj.Attributes.LayerIndex].FullPath or sc.doc.Layers[obj.Attributes.LayerIndex].Name
    return layer == COURT_LAYER


def main():
    layer_index = sc.doc.Layers.FindByFullPath(COURT_LAYER, -1)
    if layer_index < 0:
        raise RuntimeError("Missing layer {}".format(COURT_LAYER))

    to_delete = []
    for obj in sc.doc.Objects:
        if is_court_infill_to_delete(obj):
            to_delete.append(obj.Id)
    if to_delete:
        rs.DeleteObjects(to_delete)

    source_objs = []
    for obj in sc.doc.Objects:
        if obj.Attributes.LayerIndex != layer_index:
            continue
        name = obj.Attributes.Name or ""
        if name.startswith(OLD_NAME_PREFIX) or name.startswith(NEW_NAME_PREFIX):
            continue
        bb = bbox(obj)
        if abs(bb.Min.Z - SOURCE_MIN_Z) <= SOURCE_TOL and abs(bb.Max.Z - SOURCE_MAX_Z) <= SOURCE_TOL:
            source_objs.append(obj)

    source_objs.sort(key=lambda obj: (bbox(obj).Min.X, bbox(obj).Min.Y))
    if not source_objs:
        raise RuntimeError("Found no courtyard CLT modules to use as templates")

    scale_z = (TARGET_MAX_Z - TARGET_MIN_Z) / (SOURCE_MAX_Z - SOURCE_MIN_Z)
    z_map = Rhino.Geometry.Transform.Identity
    z_map.M22 = scale_z
    z_map.M23 = TARGET_MIN_Z - scale_z * SOURCE_MIN_Z

    new_ids = []
    report = []
    for index, obj in enumerate(source_objs, start=1):
        if isinstance(obj.Geometry, Rhino.Geometry.Brep):
            geom = obj.Geometry.DuplicateBrep()
        else:
            geom = obj.Geometry.Duplicate()
        geom.Transform(z_map)

        attrs = obj.Attributes.Duplicate()
        attrs.LayerIndex = layer_index
        attrs.Name = "{}{:02d}".format(NEW_NAME_PREFIX, index)
        new_id = sc.doc.Objects.Add(geom, attrs)
        new_ids.append(new_id)

        bb = geom.GetBoundingBox(True)
        report.append(
            {
                "name": attrs.Name,
                "min": [bb.Min.X, bb.Min.Y, bb.Min.Z],
                "max": [bb.Max.X, bb.Max.Y, bb.Max.Z],
            }
        )

    group_index = sc.doc.Groups.Find(GROUP_NAME, True)
    if group_index >= 0:
        sc.doc.Groups.Delete(group_index)
    group_index = sc.doc.Groups.Add(GROUP_NAME)
    for new_id in new_ids:
        sc.doc.Groups.AddToGroup(group_index, new_id)

    rs.UnselectAllObjects()
    rs.SelectObjects(new_ids)
    sc.doc.Views.Redraw()

    with open(LOG_PATH, "w") as f:
        json.dump(
            {
                "doc": sc.doc.Path,
                "deleted_old_court_infill": len(to_delete),
                "source_templates": len(source_objs),
                "created_modules": len(new_ids),
                "target_z": [TARGET_MIN_Z, TARGET_MAX_Z],
                "objects": report,
            },
            f,
            indent=2,
        )
    log("Deleted old courtyard infill: {}".format(len(to_delete)))
    log("Created courtyard wall-module infill: {}".format(len(new_ids)))
    log("Selected the new courtyard wall modules in the open file.")
    log("Did not save the file.")


try:
    main()
except Exception:
    error = traceback.format_exc()
    with open(LOG_PATH, "w") as f:
        json.dump({"error": error}, f, indent=2)
    log(error)
    raise
