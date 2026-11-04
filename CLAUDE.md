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
  and never hand-model a part. The owner authorized hybrid building: suitable whole meshes from their
  own library may be placed rigidly; never stretch or rescale them to disguise a mismatch.
- **Never invent a dimension, label, product or approval.** A brand printed on a picture is never a part name.
- **Sizes are assumptions.** Drawings aren't to scale, nothing is measured from pixels, and every scene is
  marked "Not verified". Never claim otherwise.
- **The checker is independent.** It never sees the builder's words, code decides from its ticks, and it
  is never loosened to make a scene pass. Measuring (`builder/measure.py`) runs after the build, for backend
  diagnostics only: it never changes the verdict. Part names and a model's own materials never reach the model that matches parts.
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
python -m pytest tests/test_studio.py -q -k undo            # one file, or the tests matching a name
SETOUT_VAR=var python -m uvicorn app.main:app --port 8000   # API and web/dist, with an embedded worker
modal deploy builder/sandbox.py                             # after changing the sandbox
python -m builder.loop <drawing> <name>                     # one build from the command line, saved in var/live/<name>

cd web
npm run build     # tsc -b && vite build -> web/dist, which the server serves
npm run dev       # Vite dev server, /api proxied to :8000
npx tsc -b        # type-check
npm run lint      # oxlint
npm test          # Node test runner, web/tests/*.test.ts; no network
```

To run the worker apart, start the server with `SETOUT_WORKER=external` and run `python -m app.worker` beside it
(`--once` takes one job). The variable only stops the server starting its own worker, so it does nothing on the
worker's command.

CI (`.github/workflows/checks.yml`) runs pytest, frontend logic tests, `tsc -b`, lint and the build, on Python 3.13 and Node 24.
Frontend tests use Node's built-in test runner; Python has no linter. `tests/conftest.py` points the store at a temp folder, so tests never
touch `var/`.

The owner's scenes are in `var/`. Old drawings and backups are in `~/Desktop/setout-archive`, outside the repo.

## Architecture

```
POST /api/studio    app/main.py -> app/studio.py: the drawing as a PNG artifact, a studio_build job
app/worker.py       leases the job (60 s, heartbeat 15 s, fencing, retry with backoff)
  app/studio.py     build(): runs the loop, saves steps, previews, parts and the checker's results
    builder/loop.py     Live: Muse's tool loop
      run_python, look (whole scene or named parts), inspect_source, list_parts, undo, finish
      builder/sandbox.py  live_step on Modal: replays the steps in Blender, no network
      builder/kit.py      shapes, renders and export, sent to the sandbox on every call
    builder/hybrid.py   optional shared plan -> concurrent components -> assembly check and targeted repair
    builder/library.py owner's optional DAE/ZIP hardware library, pinned into generated steps
    builder/checker.py  reads the source once (feedback reuses that list), ticks it at finish, code decides
  app/studio.py     measure(): a studio_measure job once a scene is ready, so nobody waits for it
    builder/checker.py  the contract: parts and relations read from the source, matched to objects without their names
    builder/measure.py  in the sandbox: what floats, what overlaps, which relations hold
web/src/Studio.tsx  polls the scene, then shows model.glb in three.js (viewport.ts)
```

Routes: `#/` Setout house story · `#/anatomy` redirects to `#/` · `#/app` Studio upload/gallery · `#/studio/<id>` a scene. API: `/api/studio` (GET, POST), `/api/studio/{id}` (GET, PATCH, DELETE),
`/api/studio/{id}/{drawing|preview|model.glb}`, `/diagnostics`, `/feedback`, `/undo`, `/relations/{index}`, `/api/ai/status`.

How it fits together, and what isn't obvious from any one file:

- **A scene is its list of code steps** (`studio_scenes.steps`), replayed from a clean start on every call. There
  is no saved Blender file. A step that fails isn't kept, and the error goes back to Muse. Undo drops a step.
- **Parallel builds are opt-in.** Upload with `method=hybrid`, or select the checkbox in Studio. The plan
  gives every component the same parameters and outlines. Up to three isolated model calls generate
  groups concurrently; their programs are replayed in plan order, not completion order. A complete assembly
  is rendered/exported once before the independent check. One targeted visual repair is allowed; a worse
  or failed repair keeps the earlier checked assembly. Feedback remains serial. `checks.build` records the
  plan, stage timings and selected asset ids. Components never receive geometry measurement results.
  Planning uses the source-only checklist and must assign every item. Plan/code calls use low reasoning;
  independent checking and repairs keep medium reasoning. `rounded_path` supplies generic tangent fillets
  so the model need not derive the same geometry repeatedly.
  Component code also undergoes name resolution before dispatch. Invalid sheet sections fail in the sandbox;
  errors return without rendering/exporting broken geometry. At most two execution repairs precede checking.
  Once a valid GLB exists it is viewable while checking continues; editing stays locked until the job finishes.
  Keep this opt-in: the first roof experiment needed repairs and service retries, so it did not establish
  an end-to-end speed or fidelity gain over the serial builder.
- **Library geometry is pinned.** `SETOUT_LIBRARY` defaults to `SETOUT_VAR/library`. `place_asset` accepts
  literal catalog ids only; the corresponding triangles are embedded into that component's saved step.
  `mesh_part` is a generic rigid placement helper, not a catalog of hand-built components. Assets remain
  in their original proportions. Catalog names are hints, not evidence that a part fits the drawing.
- **Concurrent calls use separate gateways.** Per-call usage/replay state must not be shared. SQLite's
  writer transaction reserves budgets and the final check's token allowance atomically; resource leases
  still limit provider concurrency. Checkpoint keys include the prompt and artifact ids.
- **The kit travels with each call,** so changing `builder/kit.py` needs no deploy. Changing
  `builder/sandbox.py` does. Muse's notes on the kit are generated from it (`loop.api()`), so a docstring there is
  prompt text, and a new shape has to go in `__all__` and in the list in `api()`.
- **Repairs keep a checked fallback.** All source items must be seen as right; `should` affects repair priority,
  not whether an error passes. Uncertain items and `not_in_source` also prevent a pass. Within a build, a worse
  repair restores the earlier steps, intents, preview and report. Essential items take priority over extras and
  smaller details. `checks.rounds` records attempts; `checks.last` belongs to the retained geometry, so it may
  differ from the last attempted round. Undo and an unchecked revision clear the old verdict.
- **Inspect before guessing.** The builder receives the source-only checklist and can crop the original image
  with `inspect_source` or frame named objects with `look`. Focused renders keep surrounding objects visible.
  Image coordinates locate a crop; they never determine physical dimensions. No geometry measurements are
  fed back to the builder, and the checker still sees only the source, its own five standard renders and
  overlapping close-ups of the cut face, rendered at higher resolution. No part names guide those crops.
- **The contract:** the source's parts and how they relate, read once and then kept, because two readings agree on
  only about 60% of their relations. Its parts are matched to objects by number and extent, and `measure.py` decides
  each relation from the geometry. It tests touching, order and overlap, never a size. A person can strike out a
  relation Muse misread through the backend `/relations/{index}` endpoint.
- **Measuring never slows a build.** Matching alone takes about 4 minutes of model time, so the build ends first
  and a `studio_measure` job follows. If it fails, the scene simply has no measurements. Low reasoning effort is
  3 to 10 times faster but matched parts wrongly, and a wrong match is a false alarm.
- **Checker reports and measurements stay in backend diagnostics.** The viewer shows the scene tree and selected
  part properties. `GET /api/studio/{id}/diagnostics` reads the saved report, model notes, progress feed, token usage and current
  build/measurement job event logs; it never starts work. Results remain in SQLite. Measurements never go to the
  builder automatically: told after every step it
  chased overlaps, and told at `finish` it rebuilt a passing parapet and the checker then failed it. Of 14 relations
  that failed across 11 scenes, 4 were Muse misreading the source. Blocking on `covers` or `on` is the owner's call,
  by eye, over more scenes.
- **`measure.py` travels with each call** like the kit, so changing it needs no deploy. Its packages are in the
  sandbox image and in `requirements.txt` for the tests. A mesh that isn't closed can have inside-out normals, so
  it never asks which way a normal points, only whether two faces are parallel.
- **Build playback:** `live_step` tags each object with the step that made it, the GLB carries that as extras, and
  `viewport.ts` plays the steps back from it.
- **Frame:** the section lies at x = 0 and the assembly runs along +x. z is up, in mm. Looking at the cut
  face, **+y is on the viewer's left**, so a drawing's left edge is the largest y. Stating this wrongly once
  made Muse mirror finished scenes.
- **Model calls go through `app/model_gateway.py`:** budgeted, checkpointed in `agent_steps`, replayed on retry.
  A retry goes back to the job's `from_step`, so the model's answers come from checkpoints and the tools run again,
  which rebuilds the scene rather than adding to it. Pictures are artifacts, so a checkpoint never holds an image. Tokens count what is paid: uncached input
  plus output (`repository.billed_tokens`). `python -m builder.loop` skips the gateway: it calls the provider
  directly, keeps only the loop's own limits, and leaves no scene in the app.
- **Two limits stop a build:** `TURNS` and `TOKENS` in `builder/loop.py`, and the job's `DEFAULT_BUDGET` in
  `app/repository.py`. Past `WRAP_UP` Muse is told to finish. `CHECK_RESERVE` leaves 50,000 tokens for the final
  check; studio model dispatches preserve that allowance too. A hard stop that cannot check an unfinished
  repair restores the earlier checked scene if available. Otherwise the partial model is explicitly unchecked.
- **Files are stored once, by content,** so two scenes can share one. `studio.delete` keeps any file another scene uses.
- **PDF extraction stays in memory:** `studio._drawing` keeps the first figure from `app/store.py` as a PNG artifact.
  It stops before later pages and creates no temporary document database, PDF or image files.
- **Long CPU work on the server starves the job heartbeat,** and a job loses its lease. Keep it out of the worker.
- **Muse reads alone.** Examples in its prompt make it copy them onto a different drawing.
- **Image embeddings can't compare line drawings.** To an image model they all look alike.
- **Presentation is optional.** `SETOUT_RENDERER=local|modal` queues a `studio_render` job atomically when
  a model becomes ready. A separate worker lane keeps rendering out of the build queue. The trusted
  `builder/presentation.py` reads only embedded GLB data, never generated Python. It bakes generic surface
  textures, preserves mesh geometry and part metadata, and renders one still. Unknown or textured materials
  stay intact. Original geometry, checker reports and measurements remain unchanged. The frontend upgrades
  surfaces in place and offers a Realistic 3D/Image switch; a failure keeps the original model usable. Existing
  scenes and retries use the Studio's Enhance 3D action (`POST /api/studio/{id}/presentation`); reads never
  start chargeable work. Duplicate clicks reuse the same job. Source model ids,
  job fencing and a renderer version prevent stale results. Artifacts and timings live in `presentation` and
  job logs. Local rendering needs Blender; Modal uses the separate `setout-presentation` app, deployed with
  `modal deploy builder/render_sandbox.py`. The still uses fixed studio lighting; it doesn't reproduce hidden
  parts or an exploded view. The base GLB stays at `/model.glb`; finished assets are `/surface.glb` and `/render`.
- **Schema changes need a migration.** `tests/test_migrations.py` fails when `app/database.py` and
  `migrations/` differ. Migrations run when `Repository` opens the store, and a store whose revision is no longer in
  `migrations/` is refused, not upgraded.
- **The landing story uses `web/src/anatomy.css`.** Its film owns scroll playback; `anatomyScene.ts` is the optional interactive explorer. Studio styling stays in `web/src/styles.css`, scoped under `.studio` and `.appbar`. The story's house is an illustration; Studio builds one local detail from a drawing at a time.

## Conventions

- Millimetres in the kit. GLB is in metres.
- Secrets live in `.env`, and are never printed.
- Runtime state lives under `SETOUT_VAR` (default `var/`, gitignored): SQLite at `v3.db`, files in `artifacts/`.
  SQLite is enough for one machine. Back the folder up, because it is not in git.
