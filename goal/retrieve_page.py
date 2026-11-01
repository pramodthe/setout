"""A local page of the retrieval results: each real drawing beside the 3D models the card search put closest. It reads
data/goal/retrieval/cards.json and spends no tokens. The models are third-party, so the page stays on this machine.

    python -m goal.retrieve_page            # writes data/goal/retrieval/page
"""
from __future__ import annotations

import json
import shutil
import sqlite3

import numpy as np

from .library import STORE
from .reference import DATA, DB, ROOT
from .retrieve_cards import cards
from .retrieve_test import OUT

PAGE = OUT / "page"
UPLOADS = DATA / "annotation" / "uploads"
WEB = next(p / "web" / "node_modules" / "three" for p in (ROOT, *ROOT.parents) if (p / "web" / "node_modules" / "three").is_dir())
THREE = ("build/three.module.js", "examples/jsm/controls/OrbitControls.js")
COLOURS = {"brick": (176, 92, 66), "block": (150, 150, 145), "concrete": (170, 170, 165), "mortar": (214, 208, 196), "thinset": (214, 208, 196), "stone": (196, 186, 166), "tile": (120, 165, 170),
           "metal": (112, 130, 150), "fixing": (70, 80, 92), "reinforcement": (70, 80, 92), "flashing": (40, 90, 150), "weep": (40, 90, 150), "drainage": (40, 90, 150), "sealant": (30, 30, 34),
           "timber": (186, 142, 92), "board": (205, 190, 150), "insulation": (232, 196, 90), "glass": (150, 200, 215)}
PLAIN = (190, 190, 190)
ZOOM_SHARE = (0.02, 0.98)  # framing skips stray far pieces, which otherwise shrink a model to a speck


def write_model(name, slug) -> dict:
    """Save a model's triangles and a colour per triangle, by the family of the component that uses each node."""
    triangles = np.load(DB / f"{name}.npz")["triangles"]
    pieces = json.loads((DB / f"{name}.json").read_text())["pieces"]
    db = sqlite3.connect(STORE / "library.db")
    family = dict(db.execute("select node, family from placements where model = ?", (name,)).fetchall())
    colour = np.tile(np.array(PLAIN, np.uint8), (len(triangles), 1))
    known = 0
    for piece in pieces:
        kind = family.get(piece["node"])
        if kind in COLOURS:
            colour[piece["tris"][0]:piece["tris"][1]] = COLOURS[kind]
            known += piece["tris"][1] - piece["tris"][0]
    centres = triangles.mean(1)
    low, high = np.quantile(centres, ZOOM_SHARE, axis=0)
    middle = (low + high) / 2
    (PAGE / f"{slug}.bin").write_bytes((triangles - middle).astype(np.float32).tobytes())
    (PAGE / f"{slug}.rgb").write_bytes(colour.tobytes())
    return {"file": slug, "triangles": len(triangles), "pieces": len(pieces), "reach": float(np.linalg.norm(high - low)) / 2, "coloured": round(known / max(len(triangles), 1), 2)}


def build() -> list:
    PAGE.mkdir(parents=True, exist_ok=True)
    for path in THREE:
        shutil.copyfile(WEB / path, PAGE / path.rsplit("/", 1)[1])
    held = dict(cards())
    models, rows = {}, []
    for drawing, answer in json.loads((OUT / "cards.json").read_text()).items():
        shutil.copyfile(UPLOADS / drawing, PAGE / drawing)
        for name in answer["names"][:3]:
            if name not in models:
                models[name] = {**write_model(name, f"m{len(models)}"), "name": name, "card": held.get(name, "")}
        rows.append({"id": drawing.rsplit("-", 1)[1].split(".")[0], "image": drawing, "read": answer["what"], "drawn": answer["built_from"], "refused": answer["verdict"] == "nothing close", "models": [models[n] for n in answer["names"][:3]]})
    (PAGE / "index.html").write_text(HTML.replace("__ROWS__", json.dumps(rows)))
    return rows


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Retrieval results</title>
<script type="importmap">{"imports":{"three":"./three.module.js"}}</script>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--soft:#66645e;--line:#dedbd3;--mark:#a8431f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,system-ui,sans-serif}
header{padding:18px 20px 12px}h1{font-size:20px;margin:0 0 4px}header p{margin:0;color:var(--soft);max-width:75ch}
nav{display:flex;gap:6px;padding:0 20px 12px;flex-wrap:wrap}button{font:inherit;padding:6px 12px;border:1px solid var(--line);background:var(--card);border-radius:6px;cursor:pointer;color:var(--ink)}
button[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}
main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.25fr);gap:14px;padding:0 20px 20px}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;min-width:0}h2{font-size:14px;margin:0 0 8px}
#drawing{width:100%;max-height:62vh;object-fit:contain;display:block;background:#fff}
#view{height:62vh;min-height:320px;background:#eceae4;border-radius:6px;overflow:hidden;position:relative}#view canvas{display:block}
#wait{position:absolute;inset:0;display:grid;place-items:center;color:var(--soft)}#wait[hidden]{display:none}
.tabs{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}.note{color:var(--mark);font-weight:600;margin:0 0 8px}small{color:var(--soft);display:block;margin-top:8px;overflow-wrap:anywhere}
@media(max-width:800px){main{grid-template-columns:minmax(0,1fr);padding:0 16px 16px}header,nav{padding-left:16px;padding-right:16px}#view{height:50vh}}
</style></head><body>
<header><h1>Retrieval on six real drawings</h1><p>Closest 3D models in the library, found by written cards. None of these is your detail. Drag to turn, scroll to zoom.</p></header>
<nav id="pick"></nav>
<main>
<section><h2>Your drawing</h2><img id="drawing" alt="Uploaded section drawing"><small id="read"></small></section>
<section><h2 id="head"></h2><p class="note" id="note"></p><div class="tabs" id="tabs"></div><div id="view"><div id="wait"></div></div><small id="card"></small></section>
</main>
<script type="module">
import * as THREE from "three";
import {OrbitControls} from "./OrbitControls.js";
const rows=__ROWS__, $=id=>document.getElementById(id), view=$("view");
const renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));view.append(renderer.domElement);
const scene=new THREE.Scene();scene.background=new THREE.Color(0xeceae4);
scene.add(new THREE.HemisphereLight(0xffffff,0x77756f,.75));const sun=new THREE.DirectionalLight(0xffffff,.8);sun.position.set(1,-2,3);scene.add(sun);
const camera=new THREE.PerspectiveCamera(35,1,.01,1000);camera.up.set(0,0,1);
const controls=new OrbitControls(camera,renderer.domElement);let mesh=null,turn=0;
const size=()=>{renderer.setSize(view.clientWidth,view.clientHeight);camera.aspect=view.clientWidth/view.clientHeight;camera.updateProjectionMatrix();draw()};
const draw=()=>renderer.render(scene,camera);controls.addEventListener("change",draw);addEventListener("resize",size);
async function show(model){
  const mine=++turn;$("wait").textContent="Loading";$("wait").hidden=false;$("card").textContent=model.name+". "+model.card;
  const [p,c]=await Promise.all([fetch(model.file+".bin"),fetch(model.file+".rgb")].map(r=>r.then(x=>x.arrayBuffer())));
  if(mine!==turn)return;
  const flat=new Float32Array(p),tint=new Uint8Array(c),colours=new Float32Array(flat.length);
  for(let t=0;t<tint.length/3;t++)for(let v=0;v<3;v++)for(let k=0;k<3;k++)colours[t*9+v*3+k]=tint[t*3+k]/255;
  const g=new THREE.BufferGeometry();g.setAttribute("position",new THREE.BufferAttribute(flat,3));g.setAttribute("color",new THREE.BufferAttribute(colours,3));g.computeVertexNormals();
  if(mesh){scene.remove(mesh);mesh.geometry.dispose()}
  mesh=new THREE.Mesh(g,new THREE.MeshStandardMaterial({vertexColors:true,roughness:.8,side:THREE.DoubleSide,flatShading:true}));scene.add(mesh);
  const r=model.reach;camera.near=r/200;camera.far=r*200;camera.position.set(r*1.9,-r*2.6,r*1.5);camera.updateProjectionMatrix();controls.target.set(0,0,0);controls.update();
  $("wait").hidden=true;draw();
}
function open(row){
  [...$("pick").children].forEach(b=>b.setAttribute("aria-pressed",b.textContent===row.id));
  $("drawing").src=row.image;$("read").textContent="Muse read this as: "+row.read;
  $("head").textContent="Closest models in the library";$("note").textContent=row.refused?"Muse said nothing here is close. These are the nearest it listed.":"Closest only. This is not your detail.";
  const tabs=$("tabs");tabs.replaceChildren();
  row.models.forEach((m,i)=>{const b=document.createElement("button");b.textContent="Closest "+(i+1);b.onclick=()=>{[...tabs.children].forEach(x=>x.setAttribute("aria-pressed",x===b));show(m)};tabs.append(b)});
  tabs.children[0].click();
}
rows.forEach(r=>{const b=document.createElement("button");b.textContent=r.id;b.onclick=()=>open(r);$("pick").append(b)});
size();open(rows[0]);
</script></body></html>
"""

if __name__ == "__main__":
    for row in build():
        print(row["id"], "refused" if row["refused"] else "listed", [(m["name"][:28], m["triangles"], m["coloured"]) for m in row["models"]])
