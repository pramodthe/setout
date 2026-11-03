"""The Setout API: a drawing goes in, Muse builds it in Blender, and the web app shows it.

    SETOUT_VAR=var python -m uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import meta_client, studio
from .runtime import ensure_worker

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
MAX_UPLOAD = 50 * 1024 * 1024


@asynccontextmanager
async def lifespan(_app):
    # A restarted server resumes queued and interrupted jobs straight away.
    if os.environ.get("SETOUT_WORKER", "embedded") == "embedded":
        ensure_worker()
    yield


app = FastAPI(title="Setout", version="0.2.0", lifespan=lifespan)


class Feedback(BaseModel):
    text: str


class Rename(BaseModel):
    title: str


class Strike(BaseModel):
    struck: bool


def _call(call, *args):
    try:
        return call(*args)
    except KeyError:
        raise HTTPException(404, "unknown scene")
    except studio.Busy as error:
        raise HTTPException(409, str(error))
    except ValueError as error:
        raise HTTPException(422, str(error)[:300])


@app.get("/api/ai/status")
async def ai_status() -> dict:
    return {"configured": meta_client.configured(), "model": meta_client.default_model()}


@app.get("/api/studio")
async def scenes() -> dict:
    return {"scenes": await run_in_threadpool(studio.listing)}


@app.post("/api/studio", status_code=202)
async def start(file: UploadFile = File(...), method: Literal["serial", "hybrid"] = Form("serial")) -> dict:
    if not meta_client.configured():
        raise HTTPException(503, "AI not configured")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file larger than 50 MB")
    return await run_in_threadpool(_call, studio.start, file.filename or "drawing", data, method)


@app.get("/api/studio/{ident}")
async def scene(ident: str) -> dict:
    return await run_in_threadpool(_call, studio.get, ident)


@app.patch("/api/studio/{ident}")
async def rename(ident: str, req: Rename) -> dict:
    return await run_in_threadpool(_call, studio.rename, ident, req.title)


@app.delete("/api/studio/{ident}", status_code=204)
async def delete(ident: str) -> None:
    await run_in_threadpool(_call, studio.delete, ident)


@app.get("/api/studio/{ident}/{which}")
async def scene_file(ident: str, which: str) -> Response:
    data, media = await run_in_threadpool(_call, studio.file_of, ident, which)
    return Response(data, media_type=media, headers={"Cache-Control": "no-store"})


@app.post("/api/studio/{ident}/feedback", status_code=202)
async def feedback(ident: str, req: Feedback) -> dict:
    return await run_in_threadpool(_call, studio.feedback, ident, req.text)


@app.post("/api/studio/{ident}/relations/{index}")
async def studio_strike(ident: str, index: int, req: Strike):
    return await run_in_threadpool(_call, studio.strike, ident, index, req.struck)


@app.post("/api/studio/{ident}/undo")
async def undo(ident: str) -> dict:
    return await run_in_threadpool(_call, studio.undo, ident)


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    page = DIST / "index.html"
    return page.read_text() if page.exists() else "<h1>Setout</h1><p>Run <code>npm run build</code> in <code>web/</code>.</p>"
