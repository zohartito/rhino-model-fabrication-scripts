import json
import os
from collections import defaultdict

import Rhino
import scriptcontext as sc


OUT = "path/to/logs/macro_clt_bottom_analysis.json"
TARGET_HINTS = [
    "03b_CLT_BACKUP_WALL_5in",
    "03bc_CLT_BACKUP_COURT",
    "TEC_CONCRETE_BASE",
    "TEC_FOUNDATION",
    "03c_WINDOW_IGU_GLASS",
    "03d_WINDOW_ALUM_FRAME",
    "03e_CLT_TO_SLAB_ANGLE_CON",
]


def layer_name(obj):
    return sc.doc.Layers[obj.Attributes.LayerIndex].FullPath


def bbox_dict(obj):
    bb = obj.Geometry.GetBoundingBox(True)
    return {
        "id": str(obj.Id),
        "name": obj.Attributes.Name or "",
        "type": obj.Geometry.GetType().Name,
        "min": [bb.Min.X, bb.Min.Y, bb.Min.Z],
        "max": [bb.Max.X, bb.Max.Y, bb.Max.Z],
        "center": [
            0.5 * (bb.Min.X + bb.Max.X),
            0.5 * (bb.Min.Y + bb.Max.Y),
            0.5 * (bb.Min.Z + bb.Max.Z),
        ],
        "dims": [bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y, bb.Max.Z - bb.Min.Z],
    }


data = {
    "doc": sc.doc.Path,
    "units": str(sc.doc.ModelUnitSystem),
    "layers": {},
}

for obj in sc.doc.Objects:
    name = layer_name(obj)
    if any(hint in name for hint in TARGET_HINTS):
        data["layers"].setdefault(name, []).append(bbox_dict(obj))

summary = {}
for name, rows in data["layers"].items():
    mins = [min(row["min"][i] for row in rows) for i in range(3)]
    maxs = [max(row["max"][i] for row in rows) for i in range(3)]
    dims = [maxs[i] - mins[i] for i in range(3)]
    # Bucket by dominant thin axis/plane and coarse coordinate.
    buckets = defaultdict(int)
    for row in rows:
        d = row["dims"]
        thin = min(range(3), key=lambda i: d[i])
        coord = row["center"][thin]
        buckets["{}@{:.2f}".format("XYZ"[thin], coord)] += 1
    summary[name] = {
        "count": len(rows),
        "min": mins,
        "max": maxs,
        "dims": dims,
        "plane_buckets": dict(sorted(buckets.items(), key=lambda item: item[0])),
    }

data["summary"] = summary

with open(OUT, "w") as f:
    json.dump(data, f, indent=2)

print("Wrote {}".format(OUT))
