# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Setout

A section drawing, or a picture of a detail, goes in. Muse builds it in live Blender, checks its own work,
and the web app shows a 3D model with every part named. Only the local assembly, never a whole building.
A playground for any trade: waterproofing is only the hard test case, so never hard-code it.

Built for the **Meta Global AI Developer Hackathon 2026**, Indie Developer track.

## Working rules

- **Never argue from the calendar.** Don't say something can't be done, or must be cut, because of a
  deadline. Scheduling is the user's call (they have a team of six).
- **Don't bring up the demo** (the hosted demo, the demo video or recording prep) until the user raises it.
- **The model is `muse-spark-1.3-contributor`** (`MODEL_API_MODEL` in `.env`). Wonder is too expensive.
- **Judge 3D by eye, beside the source.** Scores can mislead, and the owner's eye decides.

## The rules that keep it honest

- **The model writes the 3D.** `builder/kit.py` holds generic shapes only. Never add a component to it
  and never hand-model a part, which the owner ruled out.
- **Never invent a dimension, label, product or approval.** A brand printed on a picture is never a part name.
- **Sizes are assumptions.** Drawings aren't to scale, nothing is measured from pixels, and every scene is
  marked "Not verified". Never claim otherwise.
- **The checker is independent.** It never sees the builder's words, code decides from its ticks, and it
  is never loosened to make a scene pass.
- **Model code runs only in the no-network Modal sandbox,** after `check()` in `builder/loop.py`.
- **No compliance verdict on real installations.** It shows a detail, and doesn't grade real work.

## Private and third-party material

- **Never commit source documents** (PDFs, drawings, clause text, screenshots of manuals). `data/` and
  `*.pdf` are gitignored. Full rule: `.claude/rules/source-documents.md`.
- **Train only on data we own or generate,** and never on manufacturer material.
- **Submission:** public repo, Apache-2.0, own work only. Never show manual pages on screen in any video.

## Commands

Run from the repo root, with `.venv` activated.

```sh
python -m pytest tests/ -q                                  # no network: model and sandbox are stubbed
python -m pytest tests/test_studio.py -q                    # one file
SETOUT_VAR=var python -m uvicorn app.main:app --port 8000   # API and web/dist, with an embedded worker
SETOUT_WORKER=external python -m app.worker                 # or run the worker separately
modal deploy builder/sandbox.py                             # after changing the sandbox
python -m builder.loop <drawing> <name>                     # one build from the command line

cd web
npm run build     # tsc -b && vite build -> web/dist, which the server serves
npm run dev       # Vite dev server, /api proxied to :8000
npx tsc -b        # type-check
npm run lint      # oxlint
```

The owner's scenes are in `var/`. Old drawings and backups are in `~/Desktop/setout-archive`, outside the repo.

## Architecture

```
POST /api/studio    app/main.py -> app/studio.py: the drawing as a PNG artifact, a studio_build job
app/worker.py       leases the job (60 s, heartbeat 15 s, fencing, retry with backoff)
  app/studio.py     build(): runs the loop, saves steps, previews, parts and the checker's results
    builder/loop.py     Live: Muse's tool loop
      run_python, look, list_parts, undo, finish
      builder/sandbox.py  live_step on Modal: replays the steps in Blender, no network
      builder/kit.py      shapes, renders and export, sent to the sandbox on every call
    builder/checker.py  reads the source once, ticks it at finish, code decides
web/src/Studio.tsx  polls the scene, then shows model.glb in three.js (viewport.ts)
```

Routes: `#/` home · `#/studio/<id>` a scene. API: `/api/studio` (GET, POST), `/api/studio/{id}` (GET, PATCH, DELETE),
`/api/studio/{id}/{drawing|preview|model.glb}`, `/feedback`, `/undo`, `/api/ai/status`.

How it fits together, and what isn't obvious from any one file:

- **A scene is its list of code steps** (`studio_scenes.steps`), replayed from a clean start on every call. There
  is no saved Blender file. A step that fails isn't kept, and the error goes back to Muse. Undo drops a step.
- **The kit travels with each call,** so changing `builder/kit.py` needs no deploy. Changing
  `builder/sandbox.py` does.
- **Frame:** the section lies at x = 0 and the assembly runs along +x. z is up, in mm. Looking at the cut
  face, **+y is on the viewer's left**, so a drawing's left edge is the largest y. Stating this wrongly once
  made Muse mirror finished scenes.
- **Model calls go through `app/model_gateway.py`:** budgeted, checkpointed in `agent_steps`, replayed on retry.
  Pictures are artifacts, so a checkpoint never holds an image. Tokens count what is paid: uncached input
  plus output (`repository.billed_tokens`).
- **Files are stored once, by content,** so two scenes can share one. `studio.delete` keeps any file another scene uses.
- **Long CPU work on the server starves the job heartbeat,** and a job loses its lease. Keep it out of the worker.
- **Muse reads alone.** Examples in its prompt make it copy them onto a different drawing.
- **Image embeddings can't compare line drawings.** To an image model they all look alike.
- **Colours:** Blender's renders have procedural surfaces, and the GLB carries plain colours
  (`flatten_materials`). The shapes are the same.
- **Schema changes need a migration.** `tests/test_migrations.py` fails when `app/database.py` and
  `migrations/` differ.
- **`web/src/styles.css` holds all the styling,** scoped under `.studio` and `.appbar`.

## Conventions

- Millimetres in the kit. GLB is in metres.
- Secrets live in `.env`, and are never printed.
- Runtime state lives under `SETOUT_VAR` (default `var/`, gitignored): SQLite at `v3.db`, files in `artifacts/`.
  SQLite is enough for one machine. Back the folder up, because it is not in git.
