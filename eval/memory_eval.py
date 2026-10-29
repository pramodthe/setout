"""Live memory A/B with an explicit synthetic reviewer convention.

The training source prints 50mm; the held-out source prints 75mm. A fixture
reviewer chooses an unprinted slab thickness of 123mm. It may transfer only as
an assumption. This tests a correction's effect, not field accuracy.
"""
import argparse
import io
import json
import os
from pathlib import Path

from PIL import Image,ImageDraw


def run(root):
    root = Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    os.environ.update(SETOUT_VAR=str(root),SETOUT_DATABASE_URL="sqlite:///"+str(root/"v3.db"),
        SETOUT_ARTIFACT_ROOT=str(root/"artifacts"),SETOUT_WORKER="external",SETOUT_PUBLIC="0")
    from fastapi.testclient import TestClient
    from app import database as d
    from app.main import app
    from app.agent_tools import Tools
    from app.model_gateway import ModelGateway
    from app.native_program import prepare
    from app.repository import Repository,uid,sha
    from app.v3_smoke import heartbeat
    repo = Repository()
    def source(height):
        image = Image.new("RGB",(900,650),"white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((150,450,760,520),fill="#cccccc",outline="black",width=3)
        draw.rectangle((240,100,310,450),fill="#eeeeee",outline="black",width=3)
        draw.text((360,475),"SLAB",fill="black",font_size=24)
        draw.text((245,60),"WALL",fill="black",font_size=24)
        draw.line((200,100,200,450),fill="black",width=2)
        draw.line((185,100,215,100),fill="black",width=2)
        draw.line((185,450,215,450),fill="black",width=2)
        draw.text((90,240),str(height)+"mm",fill="black",font_size=28)
        png,pdf = io.BytesIO(),io.BytesIO()
        image.save(png,"PNG")
        image.save(pdf,"PDF")
        crop,document = repo.artifacts.put(png.getvalue(),"image/png"),repo.artifacts.put(pdf.getvalue(),"application/pdf")
        doc_id,fig_id = uid(),uid()
        with d.transaction(repo.engine) as con:
            repo.artifact(con,crop); repo.artifact(con,document)
            con.execute(d.documents.insert().values(id=doc_id,sha256=document["sha256"],filename="own-memory-case.pdf",pages=1,artifact_id=document["id"]))
            con.execute(d.figures.insert().values(id=fig_id,document_id=doc_id,crop_key="p0_case",page=0,width=900,height=650,rotation_deg=0,artifact_id=crop["id"]))
        return fig_id,document
    training,pdf = source(50)
    read = {"drawing_type":"section","title":"Wall/slab","rotation_deg":0,
        "dimensions":[{"id":"d0","value_mm":50,"quote_verbatim":"50mm","applies_to":"wall height"}],
        "elements":[{"id":"l0","label_verbatim":"WALL","kind":"wall","where":"above slab"},
                    {"id":"l1","label_verbatim":"SLAB","kind":"slab","where":"below wall"}]}
    read_id = uid()
    with d.transaction(repo.engine) as con:
        con.execute(d.reads.insert().values(id=read_id,figure_id=training,model="synthetic reference inventory",response=read,content_sha=sha(read),created_at=d.now(con)))
    raw = {"title":"Training convention","kind":"section","params":[
        {"name":"wall_height","value":50,"unit":"mm","kind":"exact","provenance":"drawing","source_quote":"50mm","source_dimension_id":"d0"},
        {"name":"slab_thickness","value":100,"unit":"mm","kind":"exact","provenance":"assumption"},
        {"name":"width","value":600,"unit":"mm","kind":"exact","provenance":"assumption"},
        {"name":"depth","value":200,"unit":"mm","kind":"exact","provenance":"assumption"},
        {"name":"wall_width","value":70,"unit":"mm","kind":"exact","provenance":"assumption"}],"parts":[],"relations":[],"unresolved":[]}
    for name,coords in (("slab",("0","width","-slab_thickness","0","0","depth")),("wall",("0","wall_width","0","wall_height","0","depth"))):
        raw["parts"].append({"id":name,"type":name,"role":"substrate","label":name.upper(),"shape":"box",
            "dims":[{"name":key,"expr":value} for key,value in zip(("x0","x1","y0","y1","z0","z1"),coords)],
            "features":[{"id":"height","kind":"extent","selector":{"axis":"y"},"expected_expr":"wall_height","source_dimension_id":"d0"}] if name == "wall" else []})
    program,spec,_ = prepare(raw,{"filename":"own-memory-case.pdf","sha256":pdf["sha256"],"page_index":0,"crop_id":"p0_case","page_width_pt":900,"page_height_pt":650},"memory-training-"+uid()[:12],read)
    with d.transaction(repo.engine) as con:
        original = repo.insert_version(con,detail_id=program.detail_id,expected_parent=None,program=program.model_dump(mode="json"),
            spec=spec.model_dump(mode="json"),figure_id=training,read_id=read_id,created_by="synthetic training scaffold")
    corrected = program.model_dump(mode="json")
    next(p for p in corrected["params"] if p["name"] == "slab_thickness")["value"] = 123
    lesson = "For this test convention, the reviewer selected a slab thickness of 123mm where the drawing gives none. On other drawings use that as an explicit ASSUMPTION, never a drawing or user_input value. Read the new drawing's printed wall height afresh."
    client = TestClient(app)
    review = client.post(f"/api/versions/{original['version_id']}/reviews",json={"reviewer":"Synthetic reference reviewer",
        "action":"correct","data":{"program":corrected,"lesson":lesson}})
    review.raise_for_status()
    corrected_id = review.json()["version_id"]
    memory = repo.list(d.memories,d.memories.c.source_version_id == corrected_id)[0]
    accepted = client.post(f"/api/versions/{corrected_id}/reviews",json={"reviewer":"Synthetic reference reviewer",
        "action":"activate_memory","data":{"memory_id":memory["id"]}})
    accepted.raise_for_status()
    figure,_ = source(75)
    rows = []
    for mode in ("off","on"):
        job = repo.enqueue("generate_detail",{"figure_id":figure,"memory_mode":mode})
        fence = repo.claim("memory-"+mode,only_ids=[job["id"]])
        with heartbeat(repo,fence):
            tools = Tools(repo,fence,ModelGateway(repo,fence))
            read_result = tools.run("read","read_drawing",{})
            recall = tools.run("recall","recall",{"query":"section slab wall convention"})
            brief = "Model only the two labelled parts, wall and slab. Use a parameter named slab_thickness for the undimensioned slab. Keep it an assumption, and give the printed wall height its own measured feature. Applicable lessons: "+json.dumps([m["text"] for m in recall["memories"]])
            result = tools.run("write","write_program",{"brief":brief})
            candidate = repo.get(d.versions,result["version_id"])["program"] if result.get("version_id") else {}
            thickness = next((p for p in candidate.get("params",[]) if p["name"] == "slab_thickness"),None)
            drawing = [p for p in candidate.get("params",[]) if p["provenance"] == "drawing"]
            row = {"mode":mode,"job_id":job["id"],"version_id":result.get("version_id"),"slab_thickness":thickness,
                "drawing_params":drawing,"read":read_result.get("read"),"error":result.get("error")}
            rows.append(row)
            repo.end(fence,"needs_review",{"summary":"Live correction-transfer probe; no visual/assembly approval",**row})
            print(json.dumps(row),flush=True)
    off,on = rows
    passed = bool(off["slab_thickness"] and on["slab_thickness"]) and off["slab_thickness"]["value"] != 123 and on["slab_thickness"]["value"] == 123 and on["slab_thickness"]["provenance"] == "assumption" and all(row["drawing_params"] and all(p["value"] == 75 for p in row["drawing_params"]) for row in rows)
    result = {"correction_transfer":"pass" if passed else "fail","training_printed_height":50,"held_out_printed_height":75,
        "reviewer":"synthetic reference fixture","accuracy_claim":"none; tests convention transfer and provenance", "rows":rows}
    (root/"result.json").write_text(json.dumps(result,indent=2))
    print(json.dumps({key:value for key,value in result.items() if key != "rows"}),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--var",default="var/v3-memory-live")
    run(parser.parse_args().var)
