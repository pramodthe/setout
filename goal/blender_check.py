"""An independent check of a scene against its source. One reading lists what the drawing or picture must show, before any 3D is looked at. Another ticks each item from renders of every
side of the model. Neither sees the builder's words or names, so it can only go by what is there, and code decides from the ticks.
"""
from __future__ import annotations

import json

IMPORTANCE = ("must", "should")
STATES = ("right", "wrong", "missing", "cant_tell")
ITEM = {"type": "object", "additionalProperties": False, "required": ["id", "what", "importance"], "properties": {"id": {"type": "integer"}, "what": {"type": "string"}, "importance": {"type": "string", "enum": list(IMPORTANCE)}}}
FEATURES = {"type": "object", "additionalProperties": False, "required": ["detail", "items"], "properties": {"detail": {"type": "string"}, "items": {"type": "array", "items": ITEM}}}
TICK = {"type": "object", "additionalProperties": False, "required": ["id", "state", "note"], "properties": {"id": {"type": "integer"}, "state": {"type": "string", "enum": list(STATES)}, "note": {"type": "string"}}}
TICKS = {"type": "object", "additionalProperties": False, "required": ["ticks", "not_in_source"], "properties": {"ticks": {"type": "array", "items": TICK}, "not_in_source": {"type": "array", "items": {"type": "string"}}}}
READ = """The image shows a building detail: a 2D section, or a picture of a 3D detail. Someone will build it in 3D, and you will judge that later, so list now what a correct model has to show.
List every part you can see, and how the main parts meet, each as one item a person could tick by looking at a model. Say which side or level it is on where that matters, as in "brick wall standing on the left end of the slab" or "membrane turned up the wall face".
Be concrete, and list only what this image shows. Titles, labels, logos and dimensions are not items. Don't list colours or textures.
importance: must = the detail is wrong without it. should = it is shown, but a reader would still understand the detail without it.
"""
CHECK = """The first image is the source, a building detail. The others are renders of a 3D model made from it, seen from different sides. Views named isometric show the model at an angle, and front, side and top look straight at it. The front view is the cut face.
You did not build this model. Judge each item below against the renders, from every view, and be strict.
state: right = you can see it and it is where the source has it. wrong = it is there, but in the wrong place or the wrong shape. missing = it isn't in any view. cant_tell = no view shows that area.
A part that is only hinted at, or stands in for the real shape, is wrong and not right. note: for wrong or missing, what to change, in one line. Otherwise empty.
not_in_source: anything large in the model that the source doesn't show. A model may run further than a picture, and may show what must exist behind a section, so list only what plainly doesn't belong.
Views, in order after the source: {views}
Items:
"""


def read_source(ask, drawing) -> tuple[dict, int]:
    return ask(READ, [drawing], FEATURES, "studio_features")


def verdict(features, ticks) -> dict:
    """Code decides. Every item needs an answer: one that gets none counts as not seen. A must item that is wrong or missing holds the model back."""
    answered = {t["id"]: t for t in ticks["ticks"]}
    rows = []
    for item in features["items"]:
        tick = answered.get(item["id"]) or {"state": "cant_tell", "note": "the checker gave no answer"}
        rows.append({**item, "state": tick["state"], "note": tick["note"]})
    blocking = [r for r in rows if r["importance"] == "must" and r["state"] in ("wrong", "missing")]
    return {"rows": rows, "blocking": blocking, "right": sum(r["state"] == "right" for r in rows), "total": len(rows), "not_in_source": ticks["not_in_source"], "passed": not blocking}


def check(ask, drawing, features, views: dict) -> tuple[dict, int]:
    """`views` is {name: jpeg} in the order the checker should look."""
    items = [{"id": i["id"], "what": i["what"]} for i in features["items"]]
    ticks, tokens = ask(CHECK.replace("{views}", ", ".join(views)) + json.dumps(items), [drawing, *views.values()], TICKS, "studio_check")
    return verdict(features, ticks), tokens


def fix_list(result) -> str:
    return "\n".join(f"- {row['what']}: {row['state']}. {row['note']}" for row in result["blocking"])
