"""Measure whole-assembly retrieval: does it find a relative, and refuse when there is none?

    python -m goal.retrieve_test [run name]
"""
from __future__ import annotations

import base64
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

from . import find_assembly as fa, section
from .baseline import cut
from .reference import DATA

OUT = DATA / "goal" / "retrieval"
HELD = DATA / "reference-3d" / "heldout"
FIND = ("01.030.321.3DW", "01.090.0401.3DW", "02.010.0901.3DW", "02.120.0232", "03.030.0701.3DW")
NOTHING = ("01.410.0201.3DW", "04.030.0202.3DW")
HIDE = ("06.110.0415+Tiled+Shower+Assembly+MDS+VERSION", "06.130.0102", "10.030.0631+Terra+Cotta+Lintel+Repair,+Hand+Pressed+TC", "59+Weep+vents", "71+Movement+joint")
PROMPT = fa.PROMPT.replace("give the three numbers", "give the five numbers") + """ Then give a verdict: "same" if a sheet entry is this kind of junction built the same way, "similar" if it is the same kind of junction
built differently, "nothing close" if none is. Many drawings have no match on the sheet, so never settle for the least bad one."""
SCHEMA = {**fa.SCHEMA, "required": [*fa.SCHEMA["required"], "verdict"], "properties": {**fa.SCHEMA["properties"], "verdict": {"type": "string", "enum": ["same", "similar", "nothing close"]}}}


ACCEPT = 0.6  # set before the run that uses it, and may only rise
CHECK = """Image A is a 2D section of a building detail. Image B is a section through a detail we hold. Say what kind of junction each shows. List every
construction element A shows, as layers and parts in order, and tick each one B also has in the same relative place. List the construction elements
B has that A does not. Set same_junction only if both are the same kind of junction at the same place in a building. Compare construction, never line
style or scale. Most pairs do not match, and a plausible resemblance is not a match."""
CHECK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["a_type", "b_type", "same_junction", "a_elements", "b_extra"], "properties": {
    "a_type": {"type": "string"}, "b_type": {"type": "string"}, "same_junction": {"type": "boolean"}, "b_extra": {"type": "array", "items": {"type": "string"}},
    "a_elements": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["element", "in_b"], "properties": {"element": {"type": "string"}, "in_b": {"type": "boolean"}}}}}}


def code(name) -> str:
    return ".".join(name.split("+")[0].split(".")[:2])


def picture_of(name) -> Path:
    path = HELD / f"{name}.png"
    if path.exists():
        return path
    path = fa.SHEETS / f"{name}.png"
    if not path.exists():  # a query need not be on the sheet: some are too large for the catalogue
        _, _, axis, _, cuts = cut(name)
        section.draw(cuts, axis, path)
    return path


def ask(drawing, names, page) -> dict:
    from app import meta_client
    image = fa.trimmed(drawing)
    image.thumbnail((1200, 1200))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    pictures = [{"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(b).decode()} for b in (out.getvalue(), page)]
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "medium"}, "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": PROMPT}, *pictures]}],
                                        "text": {"format": {"type": "json_schema", "name": "matches", "schema": SCHEMA, "strict": True}}}, timeout=240)
    text = "".join(p.get("text", "") for item in result["output"] if item.get("type") == "message" for p in item.get("content", []))
    answer = json.loads(text)
    answer["names"] = [names[m["number"] - 1] for m in answer["matches"] if 0 < m["number"] <= len(names)]
    answer["tokens"] = (result.get("usage") or {}).get("total_tokens") or 0
    return answer


def jpeg(path, side=1100) -> str:
    image = fa.trimmed(path)
    image.thumbnail((side, side))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


def check(drawing, candidate) -> dict:
    """One blind comparison. Code decides from the ticks: the reader never sees a score or a verdict."""
    from app import meta_client
    content = [{"type": "input_text", "text": CHECK}, {"type": "input_image", "image_url": jpeg(drawing)}, {"type": "input_image", "image_url": jpeg(candidate)}]
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "low"}, "input": [{"type": "message", "role": "user", "content": content}],
                                        "text": {"format": {"type": "json_schema", "name": "check", "schema": CHECK_SCHEMA, "strict": True}}}, timeout=200)
    text = "".join(p.get("text", "") for item in result["output"] if item.get("type") == "message" for p in item.get("content", []))
    answer = json.loads(text)
    answer["tokens"] = (result.get("usage") or {}).get("total_tokens") or 0
    matched = sum(e["in_b"] for e in answer["a_elements"])
    answer["score"] = matched / max(len(answer["a_elements"]) + len(answer["b_extra"]), 1) if answer["same_junction"] else 0.0
    return answer


def verified(query, answer) -> dict:
    drawing = picture_of(query)
    with ThreadPoolExecutor(5) as pool:
        checks = list(pool.map(lambda name: check(drawing, picture_of(name)), answer["names"][:5]))
    ranked = sorted(zip(checks, answer["names"]), key=lambda pair: -pair[0]["score"])
    accepted = [name for c, name in ranked if c["score"] >= ACCEPT]
    return {**answer, "names": [n for _, n in ranked], "verdict": "same" if accepted else "nothing close", "checks": {n: {k: c[k] for k in ("score", "same_junction", "a_type", "b_type")} for c, n in ranked},
            "tokens": answer["tokens"] + sum(c["tokens"] for c in checks), "shortlist": answer["names"]}


def contact(results, path) -> None:
    """Each query beside what came back, for the owner's eye."""
    cell, rows = 300, list(results.items())
    page = Image.new("RGB", (cell * 4, cell * len(rows)), "white")
    pen = ImageDraw.Draw(page)
    for y, (query, answer) in enumerate(rows):
        for x, name in enumerate([query, *answer["names"][:3]]):
            image = fa.trimmed(picture_of(name))
            image.thumbnail((cell - 12, cell - 34))
            page.paste(image, (x * cell + 6, y * cell + 28))
            pen.text((x * cell + 6, y * cell + 8), ("QUERY " + answer["verdict"] + " " if x == 0 else "") + name[:30], fill=(190, 0, 0) if x == 0 else (0, 0, 0))
    page.save(path, "JPEG", quality=85)


def main(run="baseline"):
    OUT.mkdir(parents=True, exist_ok=True)
    names = [n for n in fa.catalogue()]
    print(len(names), "in the catalogue", flush=True)
    jobs = [(q, names) for q in FIND + NOTHING] + [(q, [n for n in names if code(n) != code(q)]) for q in HIDE]
    pages = {tuple(group): fa.sheet(group) for _, group in jobs}
    with ThreadPoolExecutor(4) as pool:
        answers = list(pool.map(lambda job: ask(picture_of(job[0]), job[1], pages[tuple(job[1])]), jobs))
    with ThreadPoolExecutor(3) as pool:
        answers = list(pool.map(lambda pair: verified(pair[0][0], pair[1]), zip(jobs, answers)))
    results = {q: answer for (q, _), answer in zip(jobs, answers)}
    first = sum(results[q]["verdict"] != "nothing close" and code(results[q]["names"][0]) == code(q) for q in FIND if results[q]["names"])
    top = sum(any(code(n) == code(q) for n in results[q]["names"][:3]) for q in FIND)
    refused = sum(results[q]["verdict"] == "nothing close" for q in NOTHING + HIDE)
    missed = sum(results[q]["verdict"] == "nothing close" for q in FIND)
    print(json.dumps({"first": f"{first}/{len(FIND)}", "top3": f"{top}/{len(FIND)}", "refused": f"{refused}/{len(NOTHING + HIDE)}", "wrongly_refused": f"{missed}/{len(FIND)}", "tokens": sum(a["tokens"] for a in answers)}))
    for q, a in results.items():
        print(f"{q[:28]:28} {a['verdict']:14} {[(n[:16], round(a['checks'][n]['score'], 2)) for n in a['names'][:3]]}")
    (OUT / f"{run}.json").write_text(json.dumps(results, indent=1))
    contact(results, OUT / f"{run}.jpg")


if __name__ == "__main__":
    main(*sys.argv[1:2])
