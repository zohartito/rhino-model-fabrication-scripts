import json
import traceback

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc


SAVE_AS_PATH = (
    "path/to/output/model-fixed-stairs-clt.3dm"
)
LOG_PATH = "path/to/logs/fix_macro_bottom_clt_infill.json"

FRAME_LAYER = "03d_WINDOW_ALUM_FRAME"
EXT_CLT_LAYER = "03b_CLT_BACKUP_WALL_5in"
COURT_CLT_LAYER = "03bc_CLT_BACKUP_COURT"
GROUP_NAME = "CODEX_BOTTOM_CLT_INFILL_TO_CONCRETE_BASE"
NAME_PREFIX = "codex_bottom_clt_infill_"

BASE_TOP_Z = 10.0
WINDOW_BOTTOM_Z = 12.003
BOTTOM_FRAME_CENTER_Z = 12.086
BOTTOM_FRAME_TOL = 0.02


def log(message):
    print(message)


def layer_index(name):
    return sc.doc.Layers.FindByFullPath(name, -1)


def objects_by_layer(name):
    ids = rs.ObjectsByLayer(name) or []
    return ids


def bbox_values(obj_id):
    bb = rs.BoundingBox(obj_id)
    xs = [p.X for p in bb]
    ys = [p.Y for p in bb]
    zs = [p.Z for p in bb]
    return {
        "id": obj_id,
        "minx": min(xs),
        "maxx": max(xs),
        "miny": min(ys),
        "maxy": max(ys),
        "minz": min(zs),
        "maxz": max(zs),
        "cx": 0.5 * (min(xs) + max(xs)),
        "cy": 0.5 * (min(ys) + max(ys)),
        "cz": 0.5 * (min(zs) + max(zs)),
        "dx": max(xs) - min(xs),
        "dy": max(ys) - min(ys),
        "dz": max(zs) - min(zs),
    }


def delete_previous_infill():
    to_delete = []
    for obj in sc.doc.Objects:
        name = obj.Attributes.Name or ""
        if name.startswith(NAME_PREFIX):
            to_delete.append(obj.Id)
    if to_delete:
        rs.DeleteObjects(to_delete)
    group_index = sc.doc.Groups.Find(GROUP_NAME, True)
    if group_index >= 0:
        sc.doc.Groups.Delete(group_index)
    return len(to_delete)


def classify_layer(rec):
    # Exterior/north/east backup wall layer is the outer L-shaped layer:
    # y = 68.878 face and x = 265.528 face.
    if abs(rec["cy"] - 68.878) < 0.35 and rec["dy"] < 0.6:
        return EXT_CLT_LAYER
    if abs(rec["cx"] - 265.528) < 0.35 and rec["dx"] < 0.6:
        return EXT_CLT_LAYER
    return COURT_CLT_LAYER


def add_box(rec, target_layer, number):
    corners = [
        (rec["minx"], rec["miny"], BASE_TOP_Z),
        (rec["maxx"], rec["miny"], BASE_TOP_Z),
        (rec["maxx"], rec["maxy"], BASE_TOP_Z),
        (rec["minx"], rec["maxy"], BASE_TOP_Z),
        (rec["minx"], rec["miny"], WINDOW_BOTTOM_Z),
        (rec["maxx"], rec["miny"], WINDOW_BOTTOM_Z),
        (rec["maxx"], rec["maxy"], WINDOW_BOTTOM_Z),
        (rec["minx"], rec["maxy"], WINDOW_BOTTOM_Z),
    ]
    obj_id = rs.AddBox(corners)
    rs.ObjectLayer(obj_id, target_layer)
    rs.ObjectName(obj_id, "{}{:02d}".format(NAME_PREFIX, number))
    return obj_id


def main():
    log("Document: {}".format(sc.doc.Path))
    log("Units: {}".format(sc.doc.ModelUnitSystem))
    for name in [FRAME_LAYER, EXT_CLT_LAYER, COURT_CLT_LAYER]:
        if layer_index(name) < 0:
            raise RuntimeError("Missing required layer: {}".format(name))

    deleted = delete_previous_infill()
    log("Deleted previous infill objects: {}".format(deleted))

    frame_records = []
    for obj_id in objects_by_layer(FRAME_LAYER):
        rec = bbox_values(obj_id)
        if abs(rec["cz"] - BOTTOM_FRAME_CENTER_Z) <= BOTTOM_FRAME_TOL:
            frame_records.append(rec)

    frame_records.sort(key=lambda rec: (rec["cx"], rec["cy"]))
    new_objects = []
    report = []
    for index, rec in enumerate(frame_records, start=1):
        target_layer = classify_layer(rec)
        obj_id = add_box(rec, target_layer, index)
        new_objects.append(obj_id)
        report.append(
            {
                "name": "{}{:02d}".format(NAME_PREFIX, index),
                "layer": target_layer,
                "min": [rec["minx"], rec["miny"], BASE_TOP_Z],
                "max": [rec["maxx"], rec["maxy"], WINDOW_BOTTOM_Z],
            }
        )

    group_index = sc.doc.Groups.Find(GROUP_NAME, True)
    if group_index >= 0:
        sc.doc.Groups.Delete(group_index)
    group_index = sc.doc.Groups.Add(GROUP_NAME)
    for obj_id in new_objects:
        rh_obj = sc.doc.Objects.Find(obj_id)
        if rh_obj:
            sc.doc.Groups.AddToGroup(group_index, rh_obj.Id)

    rs.UnselectAllObjects()
    rs.SelectObjects(new_objects)
    sc.doc.Views.Redraw()

    log("Created bottom CLT infill objects: {}".format(len(new_objects)))
    with open(LOG_PATH, "w") as f:
        json.dump(
            {
                "doc": sc.doc.Path,
                "save_as": SAVE_AS_PATH,
                "base_top_z": BASE_TOP_Z,
                "window_bottom_z": WINDOW_BOTTOM_Z,
                "created_count": len(new_objects),
                "objects": report,
            },
            f,
            indent=2,
        )
    log("Wrote log: {}".format(LOG_PATH))
    log("Saving fixed copy to: {}".format(SAVE_AS_PATH))
    sc.doc.WriteFile(SAVE_AS_PATH, Rhino.FileIO.FileWriteOptions())
    log("Saved fixed CLT infill copy.")


try:
    main()
except Exception:
    log(traceback.format_exc())
    raise
