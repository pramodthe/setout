"""The catalogue as a page to search and browse, under gitignored data/goal/library/page. It holds renders of
third-party models, so it stays local and is never published.

    python -m goal.catalogue_page
"""
from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import picture
from .library import STORE, open_store

PAGE = STORE / "page"
SIDE = 200
BY = {"muse": "Muse (inferred)", "geometry": "its group and size", "file": "the model file"}
HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Component library</title>
<style>
:root{--bg:#f4f3ef;--card:#fff;--ink:#1d2126;--soft:#667;--line:#ddd9d0;--mark:#1f5fbf}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px;z-index:2}
h1{font-size:20px;margin:0 0 2px}#stats{color:var(--soft);margin-bottom:10px}
#search{width:100%;max-width:420px;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;background:var(--card)}
#tools{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
#chips{display:flex;gap:6px;flex-wrap:wrap}.chip{border:1px solid var(--line);background:var(--card);border-radius:99px;padding:3px 10px;cursor:pointer;font:inherit;font-size:12.5px;color:var(--ink)}
.chip.on{background:var(--mark);border-color:var(--mark);color:#fff}
main{padding:16px 20px;display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;cursor:pointer;text-align:left;padding:0;font:inherit;color:inherit}
.card img{width:100%;aspect-ratio:1;object-fit:contain;display:block;background:#fff}
.card div{padding:8px 10px 10px}.name{font-weight:600;overflow-wrap:anywhere}.meta{color:var(--soft);font-size:12.5px}
#more{grid-column:1/-1;padding:10px;font:inherit;border:1px solid var(--line);border-radius:8px;background:var(--card);cursor:pointer}
dialog{border:1px solid var(--line);border-radius:12px;max-width:620px;width:calc(100% - 32px);padding:20px;color:var(--ink);background:var(--card)}
dialog img{width:220px;float:right;margin:0 0 8px 12px}dialog h2{margin:0 0 6px;font-size:18px}dialog ul{padding-left:18px;margin:6px 0;clear:both}
dialog button{font:inherit;padding:6px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);cursor:pointer}
@media (prefers-color-scheme:dark){:root{--bg:#15181c;--card:#1f242a;--ink:#e9e7e1;--soft:#99a;--line:#333a42;--mark:#4d8fe6}}
@media (max-width:520px){header,main{padding-left:16px;padding-right:16px}main{grid-template-columns:repeat(2,1fr)}dialog img{float:none;width:100%}}
</style></head><body>
<header><h1>Component library</h1><div id="stats"></div>
<div id="tools"><input id="search" type="search" placeholder="Search: window, nut, stud, 230 mm..." aria-label="Search components">
<label><input id="parts" type="checkbox" checked> Hide fragments</label>
<label><input id="review" type="checkbox"> Needs a look only</label>
<label>Sort <select id="sort"><option value="copies">Most used</option><option value="size">Largest</option><option value="detail">Most detailed</option></select></label></div>
<div id="chips"></div></header><main id="grid"></main>
<dialog id="info"></dialog>
<script>
const DATA = __DATA__;
const STEP = 120;
let category = null, shown = STEP;
const $ = id => document.getElementById(id);
const words = c => (c.name + " " + (c.file || "") + " " + c.category + " " + c.mm.join(" ")).toLowerCase();
const mm = c => c.mm.join(" x ") + " mm";
function matches() {
  const terms = $("search").value.toLowerCase().split(/\\s+/).filter(Boolean);
  const list = DATA.parts.filter(c => (!$("parts").checked || c.category !== "fragment") && (!category || c.category === category) && (!$("review").checked || c.review) && terms.every(t => words(c).includes(t)));
  const by = {copies: c => -c.copies, size: c => -c.mm[0], detail: c => -c.triangles}[$("sort").value];
  return list.sort((a, b) => by(a) - by(b));
}
function chips() {
  const counts = {};
  for (const c of DATA.parts) if (!$("parts").checked || c.category !== "fragment") counts[c.category] = (counts[c.category] || 0) + 1;
  $("chips").replaceChildren(...Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([name, n]) => {
    const b = document.createElement("button"); b.className = "chip" + (name === category ? " on" : ""); b.textContent = name + " " + n;
    b.onclick = () => { category = category === name ? null : name; shown = STEP; draw(); }; return b; }));
}
function card(c) {
  const b = document.createElement("button"); b.className = "card";
  const img = new Image(); img.loading = "lazy"; img.alt = c.name; img.src = "thumbs/" + c.id + ".jpg";
  const text = document.createElement("div");
  const name = document.createElement("div"); name.className = "name"; name.textContent = c.name;
  const meta = document.createElement("div"); meta.className = "meta"; meta.textContent = mm(c) + " · used " + c.copies + "×";
  text.append(name, meta); b.append(img, text); b.onclick = () => open(c); return b;
}
function open(c) {
  const d = $("info"); d.replaceChildren();
  const img = new Image(); img.src = "thumbs/" + c.id + ".jpg"; img.alt = c.name;
  const h = document.createElement("h2"); h.textContent = c.name;
  const lines = [mm(c) + ", measured from the model", "Category: " + c.category, ...(c.review ? ["Needs a look: the file's names suggest " + c.said] : []), "Named by: " + c.by, "Name in the file: " + (c.file || "none"),
                 "Used " + c.copies + " times in " + c.models + " model" + (c.models === 1 ? "" : "s"), c.closed ? "Closed solid" : "Open surface", c.triangles + " triangles"];
  const ul = document.createElement("ul"); for (const l of lines) { const li = document.createElement("li"); li.textContent = l; ul.append(li); }
  const where = document.createElement("ul"); for (const m of c.in) { const li = document.createElement("li"); li.textContent = m; where.append(li); }
  const close = document.createElement("button"); close.textContent = "Close"; close.onclick = () => d.close();
  const label = document.createElement("strong"); label.textContent = "Models";
  d.append(img, h, ul, label, where, close); d.showModal();
}
function draw() {
  chips();
  const list = matches();
  $("stats").textContent = DATA.parts.length + " components from " + DATA.models + " models · showing " + list.length;
  const cards = list.slice(0, shown).map(card);
  if (list.length > shown) { const m = document.createElement("button"); m.id = "more"; m.textContent = "Show more (" + (list.length - shown) + " left)"; m.onclick = () => { shown += STEP * 3; draw(); }; cards.push(m); }
  $("grid").replaceChildren(...cards);
}
for (const id of ["search", "parts", "review", "sort"]) $(id).addEventListener("input", () => { shown = STEP; draw(); });
draw();
</script></body></html>"""


def thumb(ident) -> None:
    out = PAGE / "thumbs" / f"{ident}.jpg"
    if not out.exists():
        mesh = np.load(STORE / "parts" / f"{ident}.npy").astype(float)
        picture.view([{"triangles": mesh, "family": "unknown"}], size=SIDE).convert("RGB").save(out, "JPEG", quality=70)


def build() -> int:
    (PAGE / "thumbs").mkdir(parents=True, exist_ok=True)
    db = open_store()
    rows = db.execute("select id, coalesce(category, family), coalesce(name, muse_name), name is null, raw_name, copies, models, longest, middle, shortest, triangles, closed, review, named_by, family from components").fetchall()
    used = defaultdict(list)
    for ident, model in db.execute("select distinct component, model from placements join models on models.name = placements.model where held_out = 0 and component is not null"):
        used[ident].append(model.replace("+", " "))
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(thumb, [r[0] for r in rows]))
    parts = [{"id": r[0], "category": r[1], "name": r[2] or "unnamed", "by": BY.get(r[13], "the model file") if not r[3] else BY.get(r[13] or "muse") if r[2] else "nobody yet", "file": r[4], "review": bool(r[12]), "said": r[14],
              "copies": r[5], "models": r[6], "mm": [round(r[7] * 1000), round(r[8] * 1000), round(r[9] * 1000)], "triangles": r[10], "closed": r[11] >= 0.95, "in": sorted(used[r[0]])[:12]} for r in rows]
    data = json.dumps({"parts": parts, "models": db.execute("select count(*) from models where held_out = 0").fetchone()[0]}).replace("<", "\\u003c")
    (PAGE / "index.html").write_text(HTML.replace("__DATA__", data))
    return len(parts)


if __name__ == "__main__":
    print(build())
