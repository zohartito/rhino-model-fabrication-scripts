import math
import traceback

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System.Drawing


SAVE_AS_PATH = (
    "path/to/output/model-fixed-stairs-clt.3dm"
)

OLD_LAYERS = [
    "TEC_STAIRS",
    "TEC_STAIR_RISERS",
    "TEC_STAIR_STRINGERS",
    "TEC_STAIR_LANDINGS",
]

FIXED_LAYER = "CODEX_FIXED_STAIR_COHESIVE_MAKE2D"
GROUP_NAME = "CODEX_FIXED_STAIR_ASSEMBLY"

TREAD_THICKNESS = 0.167
STRINGER_THICKNESS_X = 0.438
STRINGER_DEPTH_Z = 0.62
RISE_TARGET = 0.5


def log(message):
    print(message)


def ensure_layer(name, color):
    index = sc.doc.Layers.FindByFullPath(name, -1)
    if index >= 0:
        layer = sc.doc.Layers[index]
        layer.Color = color
        layer.IsVisible = True
        layer.IsLocked = False
        sc.doc.Layers.Modify(layer, index, True)
        return name
    rs.AddLayer(name, color)
    return name


def clear_layer(name):
    objects = rs.ObjectsByLayer(name) or []
    if objects:
        rs.DeleteObjects(objects)


def set_old_layers_hidden():
    for name in OLD_LAYERS:
        index = sc.doc.Layers.FindByFullPath(name, -1)
        if index < 0:
            continue
        layer = sc.doc.Layers[index]
        layer.IsVisible = False
        layer.IsLocked = False
        sc.doc.Layers.Modify(layer, index, True)


def get_layer_objects(name):
    ids = rs.ObjectsByLayer(name) or []
    return ids


def bbox_record(obj_id):
    bb = rs.BoundingBox(obj_id)
    if not bb:
        return None
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
    }


def add_box_from_bbox(rec, layer, name):
    corners = [
        (rec["minx"], rec["miny"], rec["minz"]),
        (rec["maxx"], rec["miny"], rec["minz"]),
        (rec["maxx"], rec["maxy"], rec["minz"]),
        (rec["minx"], rec["maxy"], rec["minz"]),
        (rec["minx"], rec["miny"], rec["maxz"]),
        (rec["maxx"], rec["miny"], rec["maxz"]),
        (rec["maxx"], rec["maxy"], rec["maxz"]),
        (rec["minx"], rec["maxy"], rec["maxz"]),
    ]
    obj = rs.AddBox(corners)
    rs.ObjectLayer(obj, layer)
    rs.ObjectName(obj, name)
    return obj


def add_extruded_profile(points, x0, x1, layer, name):
    profile = rs.AddPolyline(points + [points[0]])
    if not profile:
        raise RuntimeError("Could not make profile for {}".format(name))
    obj = rs.ExtrudeCurveStraight(profile, (x0, 0, 0), (x1, 0, 0))
    rs.DeleteObject(profile)
    if not obj:
        raise RuntimeError("Could not extrude {}".format(name))
    rs.CapPlanarHoles(obj)
    rs.ObjectLayer(obj, layer)
    rs.ObjectName(obj, name)
    return obj


def folded_stair_profile(x, y0, y1, z0, z1, risers):
    if risers < 2:
        raise RuntimeError("Need at least two risers")
    rise = (z1 - z0) / float(risers)
    run = (y1 - y0) / float(risers - 1)
    top = [(x, y0, z0)]
    for i in range(risers):
        yi = y0 + run * i
        z_high = z0 + rise * (i + 1)
        top.append((x, yi, z_high))
        if i < risers - 1:
            y_next = y0 + run * (i + 1)
            top.append((x, y_next, z_high))
    bottom = [(px, py, pz - TREAD_THICKNESS) for px, py, pz in reversed(top)]
    return top + bottom


def stringer_profile(x, y0, y1, z0, z1):
    # A clean straight side plate. It deliberately avoids the broken stepped/
    # segmented source stringer profile that was making Make2D look jagged.
    return [
        (x, y0, z0 - STRINGER_DEPTH_Z),
        (x, y0, z0 + 0.12),
        (x, y1, z1 + 0.12),
        (x, y1, z1 - STRINGER_DEPTH_Z),
    ]


def infer_x_lanes(stair_recs):
    clusters = {}
    for rec in stair_recs:
        key = round(rec["cx"] * 2.0) / 2.0
        clusters.setdefault(key, []).append(rec)
    ranges = []
    for _key, records in clusters.items():
        if len(records) < 5:
            continue
        ranges.append(
            (
                min(r["minx"] for r in records),
                max(r["maxx"] for r in records),
                sum(r["cx"] for r in records) / len(records),
            )
        )
    ranges = sorted(ranges, key=lambda item: item[2])
    if len(ranges) < 2:
        raise RuntimeError("Could not infer the two stair x lanes")
    return [(ranges[0][0], ranges[0][1]), (ranges[-1][0], ranges[-1][1])]


def make_fixed_stair():
    doc = sc.doc
    log("Document: {}".format(doc.Path))
    log("Units: {}".format(doc.ModelUnitSystem))

    ensure_layer(FIXED_LAYER, System.Drawing.Color.FromArgb(96, 62, 32))
    clear_layer(FIXED_LAYER)
    rs.CurrentLayer(FIXED_LAYER)

    landing_ids = get_layer_objects("TEC_STAIR_LANDINGS")
    stair_ids = get_layer_objects("TEC_STAIRS")
    if not landing_ids or not stair_ids:
        raise RuntimeError("Could not find original stair/landing layers")

    landings = [bbox_record(obj_id) for obj_id in landing_ids]
    landings = [rec for rec in landings if rec]
    stairs = [bbox_record(obj_id) for obj_id in stair_ids]
    stairs = [rec for rec in stairs if rec]

    landings.sort(key=lambda rec: rec["maxz"])
    x_lanes = infer_x_lanes(stairs)
    log("Landing count: {}".format(len(landings)))
    log("Inferred x lanes: {}".format(x_lanes))

    new_objects = []
    for index, landing in enumerate(landings, start=1):
        new_objects.append(add_box_from_bbox(landing, FIXED_LAYER, "fixed_stair_landing_{:02d}".format(index)))

    flights = []
    for index in range(len(landings) - 1):
        low = landings[index]
        high = landings[index + 1]
        z0 = low["maxz"]
        z1 = high["maxz"]
        if z1 <= z0:
            continue

        # Adjacent landings alternate between the near and far side. Start at
        # the edge of the lower landing and end at the edge of the upper landing
        # so the rebuilt flights do not have the missing top runs from the old file.
        if low["cy"] < high["cy"]:
            y0 = low["maxy"]
            y1 = high["miny"]
        else:
            y0 = low["miny"]
            y1 = high["maxy"]

        x0, x1 = x_lanes[index % 2]
        risers = max(2, int(round((z1 - z0) / RISE_TARGET)))
        flights.append((index + 1, x0, x1, y0, y1, z0, z1, risers))

    for number, x0, x1, y0, y1, z0, z1, risers in flights:
        stair_profile = folded_stair_profile(x0, y0, y1, z0, z1, risers)
        new_objects.append(
            add_extruded_profile(
                stair_profile,
                x0,
                x1,
                FIXED_LAYER,
                "fixed_folded_runs_risers_flight_{:02d}".format(number),
            )
        )

        for side, sx0, sx1 in [
            ("outer_a", x0, min(x0 + STRINGER_THICKNESS_X, x1)),
            ("outer_b", max(x1 - STRINGER_THICKNESS_X, x0), x1),
        ]:
            profile = stringer_profile(sx0, y0, y1, z0, z1)
            new_objects.append(
                add_extruded_profile(
                    profile,
                    sx0,
                    sx1,
                    FIXED_LAYER,
                    "fixed_clean_stringer_flight_{:02d}_{}".format(number, side),
                )
            )

    # Recreate the central stair spine so hiding the old stringer layer does
    # not remove the vertical element visible through the stair bay. The source
    # central spine was regular; derive its x from the gap between the two stair
    # lanes and its y/z from the rebuilt flights.
    central = {
        "minx": x_lanes[0][1],
        "maxx": x_lanes[1][0],
        "miny": min(min(f[3], f[4]) for f in flights) + 0.084,
        "maxy": max(max(f[3], f[4]) for f in flights) - 0.084,
        "minz": landings[0]["maxz"],
        "maxz": landings[-1]["maxz"],
    }
    if central["maxx"] > central["minx"] and central["maxy"] > central["miny"]:
        new_objects.append(add_box_from_bbox(central, FIXED_LAYER, "fixed_central_stair_spine"))

    group_index = sc.doc.Groups.Find(GROUP_NAME, True)
    if group_index >= 0:
        sc.doc.Groups.Delete(group_index)
    group_index = sc.doc.Groups.Add(GROUP_NAME)
    for obj_id in new_objects:
        rh_obj = sc.doc.Objects.Find(obj_id)
        if rh_obj:
            sc.doc.Groups.AddToGroup(group_index, rh_obj.Id)

    set_old_layers_hidden()
    rs.SelectObjects(new_objects)
    sc.doc.Views.Redraw()
    log("Created fixed stair objects: {}".format(len(new_objects)))
    log("Old stair layers hidden: {}".format(", ".join(OLD_LAYERS)))
    log("Saving fixed copy to: {}".format(SAVE_AS_PATH))
    sc.doc.WriteFile(SAVE_AS_PATH, Rhino.FileIO.FileWriteOptions())
    log("Saved fixed stair copy.")


try:
    make_fixed_stair()
except Exception:
    log(traceback.format_exc())
    raise
