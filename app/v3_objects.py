"""Live AI-object acceptance probe; every size is an explicit test assumption.

python -m app.v3_objects --var var/v3-objects
"""
import argparse
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

from . import database as d
from .agent_tools import Tools
from .model_gateway import ModelGateway
from .repository import Repository,uid,sha
from .v3_smoke import heartbeat


def run(root):
    root = Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    repo = Repository("sqlite:///"+str(root/"v3.db"),root/"artifacts")
    poster = Image.new("RGB",(1000,600),"white")
    ImageDraw.Draw(poster).text((60,60),"WC / VANITY / BASIN\nCAD contract probe: all sizes are test assumptions",fill="black",font_size=26)
    png,pdf = io.BytesIO(),io.BytesIO()
    poster.save(png,"PNG")
    poster.save(pdf,"PDF")
    crop = repo.artifacts.put(png.getvalue(),"image/png")
    source = repo.artifacts.put(pdf.getvalue(),"application/pdf")
    document_id,figure_id,read_id = uid(),uid(),uid()
    with d.transaction(repo.engine) as con:
        repo.artifact(con,crop)
        repo.artifact(con,source)
        con.execute(d.documents.insert().values(id=document_id,sha256=source["sha256"],filename="own-object-probe.pdf",pages=1,artifact_id=source["id"]))
        con.execute(d.figures.insert().values(id=figure_id,document_id=document_id,crop_key="p0_objects",page=0,width=1000,height=600,rotation_deg=0,artifact_id=crop["id"]))
        read = {"drawing_type":"elevation","title":"Object contract probe","rotation_deg":0,"dimensions":[],"elements":[]}
        con.execute(d.reads.insert().values(id=read_id,figure_id=figure_id,model="test-authored source inventory",response=read,content_sha=sha(read),created_at=d.now(con)))
    params,parts = [],[]
    shapes = [("wc",360,650,800,"A recognisable WC: pedestal, hollow bowl and cistern, within the exact overall envelope."),
              ("vanity",800,450,750,"A vanity cabinet with an open interior, door/front divisions and a plinth; basin is a separate component."),
              ("basin",600,400,160,"A hollow wash basin with an open bowl, rim and drain bore; rounded/elliptical bowl, never a solid block.")]
    for index,(name,w,depth,h,brief) in enumerate(shapes):
        for key,value in (("w",w),("d",depth),("h",h),("wall",8),("bore",40),("center",index*1000),("gap",2)):
            params.append({"name":name+"_"+key,"value":value,"provenance":"assumption","unit":"mm","kind":"exact","note":"explicit live-probe assumption"})
        names = {key:name+"_"+key for key in ("w","d","h","wall","bore","center","gap")}
        dims = {"x0":names["center"]+"-"+names["w"]+"/2","x1":names["center"]+"+"+names["w"]+"/2",
                "y0":"0","y1":names["h"],"z0":"-"+names["d"]+"/2","z1":names["d"]+"/2"}
        parts.append({"id":name,"type":name,"role":"object","label":None,"shape":"cad","cad_brief":brief,
                      "dims":[{"name":key,"expr":value} for key,value in dims.items()],
                      "param_bindings":{key:names[key] for key in ("w","d","h","wall","bore","gap")},
                      "placement":{"x":names["center"],"y":"0","z":"0"},"install_after":[],
                      "features":[{"id":axis+"_extent","kind":"extent","selector":{"axis":axis},"expected_expr":names[key]}
                                  for axis,key in (("x","w"),("y","h"),("z","d"))]})
    from .native_program import prepare
    program,spec,_ = prepare({"title":"AI-made objects: live probe","kind":"assembly","params":params,"parts":parts,"relations":[],"unresolved":[]},
        {"filename":"own-object-probe.pdf","sha256":source["sha256"],"page_index":0,"crop_id":"p0_objects","page_width_pt":1000,"page_height_pt":600},
        "objects-"+uid()[:12],read)
    with d.transaction(repo.engine) as con:
        version = repo.insert_version(con,detail_id=program.detail_id,expected_parent=None,program=program.model_dump(mode="json"),
            spec=spec.model_dump(mode="json"),figure_id=figure_id,read_id=read_id,created_by="explicit test scaffold")
    job = repo.enqueue("revise_detail",{"detail_id":program.detail_id,"base_version_id":version["version_id"]})
    fence = repo.claim("object-probe",only_ids=[job["id"]])
    rows = []
    with heartbeat(repo,fence):
        tools = Tools(repo,fence,ModelGateway(repo,fence))
        current = version["version_id"]
        for name,w,depth,h,brief in shapes:
            result = tools.run(name,"model_component",{"version_id":current,"part_key":name,"brief":brief+
                " CadQuery X/Y centered on zero, Z from zero to p['h']. Exact overall X extent p['w'], Y extent p['d'], Z extent p['h']. "
                "Use only named p sizes and dimensionless coefficients; no physical literals or size fitting. Keep the code concise."})
            current = result.get("version_id",current)
            rows.append({"part":name,"component_id":result.get("component_id"),"placeholder":result.get("placeholder"),
                         "checks":result.get("component_checks",[]),"error":result.get("error")})
            print(json.dumps(rows[-1]),flush=True)
        report = tools.run("verify","build_and_verify",{"version_id":current})
        passed = all(row["checks"] and not row["placeholder"] and all(c["status"] == "PASS" for c in row["checks"]) for row in rows)
        result = {"objects_measured":"pass" if passed else "fail","version_id":current,"objects":rows,
                  "verification_id":report.get("verification_id"),"test_scaffold":"assumed dimensions; code generated by the live provider"}
        repo.end(fence,"needs_review",result)  # Component measurement is not assembly/visual approval.
    (root/"result.json").write_text(json.dumps(result,indent=2))
    return result


def repair(root,refine=False):
    root = Path(root).resolve()
    previous = json.loads((root/"result.json").read_text())
    repo = Repository("sqlite:///"+str(root/"v3.db"),root/"artifacts")
    current = previous["version_id"]
    version = repo.get(d.versions,current)
    job = repo.enqueue("revise_detail",{"detail_id":version["detail_id"],"base_version_id":current})
    fence = repo.claim("object-repair",only_ids=[job["id"]])
    rows = []
    with heartbeat(repo,fence):
        tools = Tools(repo,fence,ModelGateway(repo,fence))
        for old in previous["objects"]:
            refining = refine and old["part"] in ("wc","basin","vanity")
            if not refining and old["checks"] and not old["placeholder"] and all(c["status"] == "PASS" for c in old["checks"]):
                rows.append(old)
                continue
            version = repo.get(d.versions,current)
            reference_id = old.get("component_id") or version["bindings"].get(old["part"],{}).get("component_id")
            part = next(p for p in version["program"]["parts"] if p["id"] == old["part"])
            component = repo.get(d.components,reference_id) if reference_id else {"brief":part["cad_brief"],"code":""}
            old = {**old,"component_id":reference_id}
            result = tools.run("repair-"+old["part"],"model_component",{"version_id":current,"part_key":old["part"],
                "brief":component["brief"]+
                ("\nRefine the visible form. The WC needs a visibly curved oval pan/open bowl, tapered pedestal and distinct cistern. The basin needs a curved, tapered oval vessel with open bowl/rim/drain. Cabinet doors and pan face negative Y; cistern/back is positive Y. Use directly sized elliptical profiles, lofts and hollow cuts or shells; no box as the exterior of the bowl/vessel. Cistern and cabinet may be boxes. No unit-sphere scaling or arbitrary affine transforms. Every physical length must come from p values, including wall/gap. " if refining else "\nRepair the generated code after independent measurement found: ")+json.dumps(old["checks"])+
                "\nPreserve the declared overall envelope; fix the construction and axes rather than scaling/fitting. Previous code:\n"+component["code"]})
            current = result.get("version_id",current)
            row = {"part":old["part"],"component_id":result.get("component_id") or old["component_id"],"placeholder":result.get("placeholder"),
                   "checks":result.get("component_checks",[]),"error":result.get("error")}
            rows.append(row)
            print(json.dumps(row),flush=True)
        report = tools.run("verify","build_and_verify",{"version_id":current})
        passed = all(row["checks"] and not row["placeholder"] and all(c["status"] == "PASS" for c in row["checks"]) for row in rows)
        result = {"objects_measured":"pass" if passed else "fail","version_id":current,"objects":rows,
            "verification_id":report.get("verification_id"),"history":previous.get("history",[])+[previous["objects"]],
            "test_scaffold":previous["test_scaffold"]}
        repo.end(fence,"needs_review",result)
    (root/"result.json").write_text(json.dumps(result,indent=2))
    return result


def render_existing(root):
    import os,socket,subprocess,sys,time,urllib.request
    from .render import Renderer
    root = Path(root).resolve()
    result = json.loads((root/"result.json").read_text())
    repo = Repository("sqlite:///"+str(root/"v3.db"),root/"artifacts")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1",0))
        port = probe.getsockname()[1]
    environment = {**os.environ,"SETOUT_VAR":str(root),"SETOUT_DATABASE_URL":str(repo.engine.url),
        "SETOUT_ARTIFACT_ROOT":str(root/"artifacts"),"SETOUT_WORKER":"external","SETOUT_PUBLIC":"0"}
    with (root/"render-server.log").open("w") as log:
        server = subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--port",str(port)],env=environment,stdout=log,stderr=log)
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(200):
                try:urllib.request.urlopen(base+"/api/ai/status",timeout=1).close();break
                except OSError:
                    if server.poll() is not None:raise RuntimeError("object renderer server failed")
                    time.sleep(.05)
            report = repo.get(d.verifications,result["verification_id"])
            images = Renderer(repo,base).render(result["version_id"],report["manifest_sha"])
            paths = []
            for image in images:
                path = root/(image["view"]+("-tags" if image["tags"] else "")+".jpg")
                path.write_bytes(repo.artifacts.read(image["artifact"]["id"]))
                paths.append(str(path))
            print(json.dumps({"rendered":paths},indent=2))
        finally:
            server.terminate();server.wait(timeout=15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--var",default="var/v3-objects")
    parser.add_argument("--repair",action="store_true")
    parser.add_argument("--refine",action="store_true")
    parser.add_argument("--render",action="store_true")
    args = parser.parse_args()
    if args.render:render_existing(args.var)
    elif args.repair or args.refine:repair(args.var,args.refine)
    else:run(args.var)
