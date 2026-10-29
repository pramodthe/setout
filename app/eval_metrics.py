"""Comparable execution metrics; unavailable human fidelity stays explicit."""
from collections import Counter
from . import database as d


def result_row(repo, job_id, seconds):
    job, run = repo.get(d.jobs,job_id),repo.run(job_id)
    version_id = run["working_version_id"]
    reports = repo.list(d.verifications,d.verifications.c.version_id == version_id,order=d.verifications.c.created_at)
    checks = reports[-1]["checks"] if reports else []
    critics = repo.list(d.critics,d.critics.c.version_id == version_id,order=d.critics.c.created_at)
    steps = repo.list(d.steps,d.steps.c.run_id == run["id"])
    usage = Counter()
    for step in steps:
        tokens = (step.get("response") or {}).get("usage") or {}
        usage["input"] += tokens.get("input_tokens",0)
        usage["output"] += tokens.get("output_tokens",0)
        usage["cached"] += (tokens.get("input_tokens_details") or {}).get("cached_tokens",0)
    version = repo.get(d.versions,version_id) if version_id else None
    read = repo.get(d.reads,version["drawing_read_id"])["response"] if version and version["drawing_read_id"] else {}
    if not read:
        operations = repo.list(d.operations,d.operations.c.run_id == run["id"],d.operations.c.tool_name == "read_drawing")
        read = next((operation["result"]["read"] for operation in operations
                     if operation.get("result") and "read" in operation["result"]),{})
    labels = {p.get("label") for p in version["program"]["parts"] if p.get("label")} if version else set()
    printed = {e["label_verbatim"] for e in read.get("elements",[])}
    # Labels the READ classed as a layer or object: zone names, notes and
    # table text are not parts. None when the read has no categories.
    part_labels = {e["label_verbatim"] for e in read.get("elements",[]) if e.get("category") in ("layer","object")}
    buildable = bool(reports) and not any(c["id"].startswith(("cad-","object-form-","fixture-form-","B-built-","B-mesh-")) and
        c["status"] in ("FAIL","UNVERIFIED") for c in checks)
    return {"job_id":job_id,"version_id":version_id,"state":job["state"],"error":job["error"],
            "seconds":round(seconds,2),"tokens":run["consumed"].get("tokens",0),"token_usage":dict(usage),
            "reserved":run["reserved"],"scene_available":bool(reports),"buildable":buildable,
            "required_checks":dict(Counter(c["status"] for c in checks if c.get("required"))),
            "source_dimensions_read":len(read.get("dimensions",[])),
            "source_dimensions_pass":sum(c["id"].startswith("source-dimension-") and c["status"] == "PASS" for c in checks),
            "labels_read":len(printed),"labels_retained":len(printed & labels),
            "part_labels_read":len(part_labels) if part_labels else None,
            "part_labels_retained":len(part_labels & labels) if part_labels else None,
            "critic_verdict":critics[-1]["verdict"] if critics else "not_run",
            "cad_attempts":run["consumed"].get("cad_calls",0),"render_attempts":run["consumed"].get("render_calls",0),
            "reviewer_fidelity":None,"issues":(job["result"] or {}).get("open_issues",[])}
