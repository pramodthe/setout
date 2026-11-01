"""A local page for a built detail: the drawing beside its 3D, with every part's source and the plan's relations. The catalogue's models are third-party, so this stays on this machine.

    python -m goal.framework_page <drawing> [...]      # writes data/goal/framework/page
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

from . import plan as planner
from .engine import RUN, Scene
from .part_search import OUT
from .retrieve_page import COLOURS, PLAIN, THREE, WEB

PAGE = OUT / "page"
MORE = {"membrane": (45, 52, 70), "adhesive": (120, 105, 125), "metal trim": (112, 130, 150), "wall tie": (225, 90, 40), "fixing": (225, 90, 40), "weep": (40, 150, 200), "mortar": (222, 216, 204), "board": (205, 190, 150)}


def write(drawing) -> dict:
    scene = Scene(drawing)
    scene.build()
    state = scene.contract()
    clashes = scene.clashes()
    slug = Path(drawing).stem.rsplit("-", 1)[1]
    shutil.copyfile(drawing, PAGE / f"{slug}.png")
    names, parts = {p["id"]: p for p in scene.plan["parts"]}, []
    gathered: dict = {}
    for item in scene.items:
        gathered.setdefault(item["part"], []).append(item)
    middle = None
    for k, (ident, items) in enumerate(gathered.items()):
        triangles = np.vstack([i["triangles"] for i in items]).astype(np.float32)
        middle = middle if middle is not None else None
        part = names.get(ident, {"name": "head joints", "category": "mortar", "seen": "drawn", "form": "units", "reason": "the joints between units"})
        colour = {**COLOURS, **MORE}.get(part["category"], PLAIN)
        path = f"{slug}-{k}.bin"
        (PAGE / path).write_bytes(triangles.tobytes())
        parts.append({"file": path, "name": part["name"], "category": part["category"], "colour": colour, "seen": part["seen"], "form": part["form"], "reason": part["reason"], "pieces": len(items), "source": items[0]["source"]})
    every = np.vstack([np.vstack([i["triangles"] for i in items]) for items in gathered.values()]).reshape(-1, 3)
    return {"id": slug, "image": f"{slug}.png", "assembly": scene.plan["assembly"], "parts": parts, "centre": ((every.min(0) + every.max(0)) / 2).tolist(), "reach": float(np.linalg.norm(every.max(0) - every.min(0))) / 2,
            "run": RUN, "notes": scene.notes, "unsure": scene.plan["unsure"], "tokens": scene.plan["tokens"], "contract": state, "clashes": [list(c) for c in clashes], "faults": planner.faults(scene.plan, scene.regions)}


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Planned detail</title>
<script type="importmap">{"imports":{"three":"./three.module.js"}}</script>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--soft:#66645e;--line:#dedbd3;--mark:#a8431f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,system-ui,sans-serif}
header{padding:16px 20px 8px}h1{font-size:20px;margin:0}header p{margin:2px 0 0;color:var(--soft)}
nav{display:flex;gap:6px;padding:6px 20px;flex-wrap:wrap;align-items:center}button{font:inherit;padding:6px 12px;border:1px solid var(--line);background:var(--card);border-radius:6px;cursor:pointer;color:var(--ink)}
button[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}label{color:var(--soft)}
main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr);gap:14px;padding:6px 20px}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;min-width:0}h2{font-size:14px;margin:0 0 8px}
#drawing{width:100%;max-height:68vh;object-fit:contain;display:block}#view{height:68vh;min-height:320px;border-radius:6px;overflow:hidden}#view canvas{display:block}
.below{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:14px;padding:8px 20px 24px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:4px 8px 4px 0;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--soft);font-weight:500}
.dot{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:6px;vertical-align:-1px}.implied{color:var(--mark);font-weight:600}ul{margin:0;padding-left:18px;font-size:13px}
@media(max-width:800px){main,.below{grid-template-columns:minmax(0,1fr);padding-left:16px;padding-right:16px}header,nav{padding-left:16px;padding-right:16px}#view{height:50vh}}
</style></head><body>
<header><h1>Planned detail</h1><p id="what"></p></header>
<nav><span id="pick"></span><button id="angle">3D view</button><button id="along">Look along the run</button><label>Pull apart <input id="spread" type="range" min="0" max="100" value="0"></label></nav>
<main><section><h2>Your drawing</h2><img id="drawing" alt="Uploaded section drawing"></section><section><div id="view"></div></section></main>
<div class="below"><section><h2>Parts</h2><table id="parts"></table></section><section><h2>Relationships and checks</h2><p id="score"></p><table id="contract"></table><h2 style="margin-top:12px">Assumptions and notes</h2><ul id="notes"></ul></section></div>
<script type="module">
import * as THREE from "three";
import {OrbitControls} from "./OrbitControls.js";
const rows=__ROWS__, $=id=>document.getElementById(id), view=$("view");
const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));view.append(renderer.domElement);
const scene=new THREE.Scene();scene.background=new THREE.Color(0xeceae4);
scene.add(new THREE.HemisphereLight(0xffffff,0x77756f,.8));const sun=new THREE.DirectionalLight(0xffffff,.9);sun.position.set(1,1,3);scene.add(sun);
const camera=new THREE.PerspectiveCamera(30,1,.01,500);camera.up.set(0,0,1);
const controls=new OrbitControls(camera,renderer.domElement);const group=new THREE.Group();scene.add(group);let row=null,meshes=[];
const draw=()=>renderer.render(scene,camera);controls.addEventListener("change",draw);
const size=()=>{renderer.setSize(view.clientWidth,view.clientHeight);camera.aspect=view.clientWidth/view.clientHeight;camera.updateProjectionMatrix();draw()};addEventListener("resize",size);
const frame=side=>{const r=row.reach,c=row.centre;camera.fov=side?4:30;camera.updateProjectionMatrix();controls.target.set(...c);camera.position.set(...(side?[c[0]+r*30,c[1],c[2]]:[c[0]-r*2.3,c[1]+r*2.9,c[2]+r*1.7]));controls.update();draw()};
async function open(r){row=r;[...$("pick").children].forEach(b=>b.setAttribute("aria-pressed",b.textContent===r.id));$("drawing").src=r.image;$("what").textContent=r.assembly+". Plan and placement by Muse and the engine; "+r.tokens.toLocaleString()+" tokens.";
  meshes.forEach(m=>{group.remove(m);m.geometry.dispose()});meshes=[];
  const t=$("parts");t.innerHTML="<tr><th>Show</th><th>Part</th><th>Built from</th><th>Pieces</th></tr>";
  for(const [k,p] of r.parts.entries()){const flat=new Float32Array(await (await fetch(p.file)).arrayBuffer());const g=new THREE.BufferGeometry();g.setAttribute("position",new THREE.BufferAttribute(flat,3));g.computeVertexNormals();
    const m=new THREE.Mesh(g,new THREE.MeshStandardMaterial({color:new THREE.Color(`rgb(${p.colour.join(",")})`),roughness:.85,side:THREE.DoubleSide,flatShading:true}));m.userData.k=k;group.add(m);meshes.push(m);
    const tr=document.createElement("tr");tr.innerHTML=`<td><input type="checkbox" checked aria-label="Show ${p.name}"></td><td><span class="dot" style="background:rgb(${p.colour.join(",")})"></span>${p.name}${p.seen==="implied"?' <span class="implied">implied</span>':""}<br><small>${p.reason}</small></td><td>${p.source}</td><td>${p.pieces}</td>`;tr.querySelector("input").onchange=e=>{m.visible=e.target.checked;draw()};t.append(tr)}
  const bind=r.contract.filter(c=>c.after!=="not binding");$("score").textContent=`Relationships broken: ${bind.filter(c=>c.before==="broken").length} before repair, ${bind.filter(c=>c.after==="broken").length} after, of ${bind.length}. Part clashes: ${r.clashes.length}.`;
  $("contract").innerHTML="<tr><th>Relationship</th><th>Before</th><th>After</th></tr>"+r.contract.map(c=>`<tr><td>${c.subject} ${c.relation.replace("_"," ")} ${c.target}${c.second_target?" and "+c.second_target:""}</td><td>${c.before}</td><td>${c.after}</td></tr>`).join("");
  $("notes").innerHTML=[...r.notes,...r.unsure.map(u=>"Muse unsure: "+u),...r.faults.map(f=>"Plan fault: "+f),...r.clashes.map(c=>`Clash: ${c[0]} and ${c[1]}`)].map(n=>`<li>${n}</li>`).join("");
  spread();size();frame(false)}
function spread(){const s=$("spread").value/100*row.reach*1.6;meshes.forEach((m,k)=>{m.position.set(0,0,0);m.position.x=-s*(k/Math.max(meshes.length-1,1))});draw()}
$("spread").oninput=spread;$("angle").onclick=()=>frame(false);$("along").onclick=()=>frame(true);
rows.forEach(r=>{const b=document.createElement("button");b.textContent=r.id;b.onclick=()=>open(r);$("pick").append(b)});
size();open(rows[0]);
</script></body></html>
"""

if __name__ == "__main__":
    PAGE.mkdir(parents=True, exist_ok=True)
    for path in THREE:
        shutil.copyfile(WEB / path, PAGE / path.rsplit("/", 1)[1])
    built = [write(d) for d in sys.argv[1:]]
    (PAGE / "index.html").write_text(HTML.replace("__ROWS__", json.dumps(built, default=float)))
    for row in built:
        bind = [c for c in row["contract"] if c["after"] != "not binding"]
        print(row["id"], "parts", len(row["parts"]), "broken before", sum(c["before"] == "broken" for c in bind), "after", sum(c["after"] == "broken" for c in bind), "of", len(bind), "clashes", row["clashes"], "faults", row["faults"])
        print(*row["notes"], sep="\n")
