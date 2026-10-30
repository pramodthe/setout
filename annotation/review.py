"""The review page: a small stdlib server on 127.0.0.1, one drawing at a time.

Everything a reviewer does is posted here, applied to the sheet's record, saved, and
logged with the seconds it took. Nothing leaves the machine and the page loads nothing
from anywhere else. It binds to 127.0.0.1 only and writes only to the annotation store.

A web page in the same browser can reach 127.0.0.1, so a request must name this server
as its Host, and a write must carry the token printed into the page at start-up. Other
sites cannot read that token, and a custom header cannot be sent cross-site without a
preflight this server never answers.
"""
from __future__ import annotations

import copy
import json
import re
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import ontology

from . import propagate, sheets

HERE = Path(__file__).resolve().parent
HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY = 1 << 20
MAX_SECONDS = 3600.0  # the page stops counting when idle; this guards the record anyway
UNDO_DEPTH = 64
ROUTE = re.compile(r"^/api/sheets/([a-z0-9][a-z0-9-]{0,63})(?:/([a-z]+))?$")
SECURITY = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"


def class_list() -> list[dict]:
    onto = ontology.load()
    rows = []
    for class_id in onto.component_ids():
        item = onto.component(class_id)
        rows.append({"id": class_id, "name": item.name, "role": item.role, "aliases": list(item.aliases)})
    return rows


def page_html(token: str) -> str:
    html = (HERE / "page.html").read_text()
    css = (HERE / "page.css").read_text()
    js = (HERE / "page.js").read_text()
    return html.replace("/*CSS*/", css).replace("/*JS*/", js).replace("__TOKEN__", token)


class Reviewer:
    """Applies decisions to the store. One lock, so two tabs cannot lose each other's work."""

    def __init__(self, store):
        self.store = sheets.require_store(store)
        self.lock = threading.Lock()
        self.undo: dict[str, list] = {}
        self.known = {row["id"] for row in class_list()}

    def overview(self) -> list[dict]:
        return [sheets.summary(sheets.read_sheet(self.store, sheet_id)) for sheet_id in sheets.sheet_ids(self.store)]

    def sheet(self, sheet_id) -> dict:
        record = sheets.read_sheet(self.store, sheet_id)
        return {**record, "summary": sheets.summary(record)}

    def act(self, sheet_id, action, body) -> dict:
        with self.lock:
            record = sheets.read_sheet(self.store, sheet_id)
            if action == "undo":
                return self._undo(sheet_id, record)
            before = copy.deepcopy(record)
            seconds = self._seconds(body)
            result = self._apply(record, action, body, seconds)
            sheets.write_sheet(self.store, record)
            stack = self.undo.setdefault(sheet_id, [])
            stack.append(before)
            del stack[:-UNDO_DEPTH]
            sheets.log_event(self.store, sheet_id, {"action": action, "seconds": seconds, **result,
                                                    **{k: body[k] for k in ("region", "regions", "class_id") if k in body}})
            if sheets.summary(record)["done"] and not sheets.summary(before)["done"]:
                result["carried"] = propagate.carry(self.store, sheet_id)  # a person finished it: share what they said
            return {**record, "summary": sheets.summary(record), "result": result}

    def _undo(self, sheet_id, record) -> dict:
        stack = self.undo.get(sheet_id) or []
        if not stack:
            raise sheets.Refused("nothing to undo")
        previous = stack.pop()
        sheets.write_sheet(self.store, previous)
        sheets.log_event(self.store, sheet_id, {"action": "undo"})
        return {**previous, "summary": sheets.summary(previous), "result": {"undone": True}}

    @staticmethod
    def _seconds(body) -> float:
        value = body.get("seconds", 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        return round(min(max(float(value), 0.0), MAX_SECONDS), 3)

    def _apply(self, record, action, body, seconds) -> dict:
        if action == "merge":
            ids = [str(i) for i in body.get("regions") or []]
            spent = sum(sheets.region_of(record, i).get("seconds", 0.0) for i in dict.fromkeys(ids)) + seconds
            fresh = sheets.merge(record, ids)
            sheets.region_of(record, fresh)["seconds"] = round(spent, 3)
            return {"created": [fresh]}
        if action == "split":
            region = sheets.region_of(record, str(body.get("region")))
            spent = region.get("seconds", 0.0) + seconds
            made = sheets.split(record, region["id"], body["start"], body["end"])
            for new_id in made:
                sheets.region_of(record, new_id)["seconds"] = round(spent / len(made), 3)
            return {"created": made}
        if action == "flag":
            note = str(body.get("note") or "").strip()[:500]
            record["excluded_from_export"] = note or None if body.get("exclude", True) else None
            if body.get("exclude", True) and not record["excluded_from_export"]:
                record["excluded_from_export"] = "left out by a reviewer"
            return {"excluded": bool(record["excluded_from_export"])}
        if action == "acceptall":
            accepted = sheets.accept_all(record)
            for region_id in accepted:
                region = sheets.region_of(record, region_id)
                region["seconds"] = round(region.get("seconds", 0.0) + seconds / len(accepted), 3)
            return {"accepted": accepted}
        region = sheets.region_of(record, str(body.get("region")))
        region["seconds"] = round(region.get("seconds", 0.0) + seconds, 3)
        if action == "accept":
            sheets.accept(record, region["id"])
        elif action == "correct":
            sheets.correct(record, region["id"], str(body.get("class_id")), self.known)
        elif action == "reject":
            sheets.reject(record, region["id"])
        elif action == "restore":
            sheets.restore(record, region["id"])
        else:
            raise sheets.Refused(f"unknown action: {action}")
        return {"state": region["proposal"]["state"], "rejected": region["rejected"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "setout-review/1"
    sys_version = ""

    def log_message(self, *_):  # a reviewer's terminal stays quiet
        pass

    @property
    def app(self) -> Reviewer:
        return self.server.reviewer

    def _send(self, status, payload, kind="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", SECURITY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        if self.headers.get("Host") in (f"{HOST}:{port}", f"localhost:{port}"):
            return True
        self._send(HTTPStatus.FORBIDDEN, {"error": "wrong host"})
        return False

    def do_GET(self):
        if not self._host_ok():
            return
        path = self.path.split("?", 1)[0]
        try:
            if path == "/":
                return self._send(HTTPStatus.OK, page_html(self.server.token).encode(), "text/html; charset=utf-8")
            if path == "/favicon.ico":
                return self._send(HTTPStatus.NO_CONTENT, b"")
            if path == "/api/sheets":
                return self._send(HTTPStatus.OK, self.app.overview())
            if path == "/api/classes":
                return self._send(HTTPStatus.OK, class_list())
            found = ROUTE.match(path)
            if found and found.group(2) is None:
                return self._send(HTTPStatus.OK, self.app.sheet(found.group(1)))
            if found and found.group(2) == "image":
                image = sheets.sheet_dir(self.app.store, found.group(1)) / "sheet.png"
                if image.is_file():
                    return self._send(HTTPStatus.OK, image.read_bytes(), "image/png")
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except sheets.Refused as refused:
            self._send(HTTPStatus.NOT_FOUND, {"error": str(refused)})

    def do_POST(self):
        if not self._host_ok():
            return
        if not secrets.compare_digest(self.headers.get("X-Setout-Token", ""), self.server.token):
            return self._send(HTTPStatus.FORBIDDEN, {"error": "bad token"})
        found = ROUTE.match(self.path)
        if not found or not found.group(2):
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected an object")
            self._send(HTTPStatus.OK, self.app.act(found.group(1), found.group(2), body))
        except sheets.Refused as refused:
            self._send(HTTPStatus.CONFLICT, {"error": str(refused)})
        except (ValueError, KeyError, TypeError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": f"{type(error).__name__}: {error}"})


def make_server(store, port=DEFAULT_PORT) -> ThreadingHTTPServer:
    """A server bound to 127.0.0.1. Port 0 picks a free one."""
    server = ThreadingHTTPServer((HOST, port), Handler)
    server.reviewer = Reviewer(store)
    server.token = secrets.token_urlsafe(24)
    return server


def serve(store, port=DEFAULT_PORT, log=print) -> None:
    server = make_server(store, port)
    log(f"review page: http://{HOST}:{server.server_address[1]}/  (store: {store}; Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

