import json
import traceback

import Rhino
import scriptcontext as sc


SAVE_AS_PATH = (
    "path/to/output/model-fixed-stairs-clt-final.3dm"
)
LOG_PATH = "path/to/logs/save_macro_current.json"


def layer_summary():
    rows = []
    for i in range(sc.doc.Layers.Count):
        layer = sc.doc.Layers[i]
        lname = layer.FullPath or layer.Name or ""
        count = 0
        infill = 0
        for obj in sc.doc.Objects:
            if obj.Attributes.LayerIndex == i:
                count += 1
                if (obj.Attributes.Name or "").startswith("codex_bottom_clt_infill_"):
                    infill += 1
        if (
            "STAIR" in lname
            or "CLT_BACKUP" in lname
            or "CODEX" in lname
            or infill
        ):
            rows.append(
                {
                    "layer": lname,
                    "visible": layer.IsVisible,
                    "locked": layer.IsLocked,
                    "count": count,
                    "codex_bottom_infill": infill,
                }
            )
    return rows


try:
    before = layer_summary()
    ok = sc.doc.WriteFile(SAVE_AS_PATH, Rhino.FileIO.FileWriteOptions())
    with open(LOG_PATH, "w") as f:
        json.dump(
            {
                "doc": sc.doc.Path,
                "save_as": SAVE_AS_PATH,
                "write_ok": bool(ok),
                "layers": before,
            },
            f,
            indent=2,
        )
    print("write_ok={} path={}".format(ok, SAVE_AS_PATH))
except Exception:
    with open(LOG_PATH, "w") as f:
        json.dump({"error": traceback.format_exc()}, f, indent=2)
    raise
