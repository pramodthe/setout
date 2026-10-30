# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Setout — project instructions

A playground for construction installation details. A 2D technical drawing
becomes a validated component spec, which drives dimension-based 3D, assembly,
install sequence and a verification report, with every value traceable to its
source. Later goal: point a phone at a junction and see the correct detail in 3D.

**The engine is trade-agnostic.** Waterproofing (door sills, shower details) is the
FIRST detail library, not the product. Any installation detail with numeric rules
can become a library: cladding, flashing, anchors, insulation. Do not hard-code
waterproofing assumptions into the generators, the schema or the viewer.

Built for the **Meta Global AI Developer Hackathon 2026**, Indie Developer track.

## Working rules

- **Never argue from the calendar.** Don't say something can't be done, or must
  be cut or deferred, because of a deadline or the days left. Plan and build the
  full scope; scheduling is the user's call (they have a team of six). User
  instruction, 29 Oct 2026.
- **Don't bring up the demo** (the hosted demo, the demo video or recording
  prep) until the user raises it: no questions, suggestions or status lines
  about it. User instruction, 29 Oct 2026.

## Where things are

| | |
|---|---|
| What | Any drawing in a PDF → Muse agent (tools, memory, persistent jobs) → verified 3D versions in a database → library, 3D viewer, AI guide |
| Code | `app/` (FastAPI, worker, agent), `migrations/`, `web/` (React+TS), `tests/`, `eval/`, `e2e/`. Drawing segmentation: `synthetic/`, `tools/`, `training/`, `dashboard/` |
| Source of numbers | `app/seed.py` (two hand-transcribed seeds) and AI-written programs gated against the drawing (database versions) |
| Deps | `requirements.txt` (pinned) |

**`docs/` is private.** It is gitignored and exists only on the owner's machine,
so never commit it, and never quote it in a PR or any public file. It holds
`PLAN_V3.md` (the v3 design), `V3_IMPLEMENTATION.md`, `V3_EVAL.md`,
`MULTI_AGENT_SPEC.md` (next, not built), `DETAIL_WORLD_MODEL_SPEC.md`,
`instruction.md` (how to build datasets and train) and `STATUS.md`.

Read the plan doc before you propose architecture changes. `docs/STATUS.md` is
the running log, newest entry first. Check it for current state and add an entry
when a milestone lands.

## The invariant — do not break this

**3D geometry is generated from named values, never placed by hand.**
(Adopted with v3, 29 Oct 2026; it replaced "the model never writes geometry".)

- **Every value is named and sourced.** Every param carries `provenance`
  (`drawing` / `manufacturer_doc` / `user_input` / `assumption`). Assumptions
  and unresolved items must **never** produce a verified PASS.
- **Decided, not yet in code:** a fifth provenance, `inferred`, for parts a model
  fills in that the drawing does not show (e.g. the door frame behind a threshold
  section). It is labelled inference, never `drawing`: shown ghosted with its
  source and confidence, never a verified PASS, and user confirmation turns it
  into `user_input`. Spec: `docs/DETAIL_WORLD_MODEL_SPEC.md`.
- **Layers and substrates** (anything built up in place or laid as a sheet or
  board) are exact primitives from the program grammar (`app/program.py`,
  `app/native_program.py`): box, sheet, wedge, profile, revolve, sweep. The model
  writes the program; our compiler builds the geometry.
- **Objects** (fixings, brackets, trims, grates, fixtures...) are parametric
  CadQuery `build(p)` written by the model and run only in the no-network Modal
  sandbox (`app/cad_components.py`, `app/cad_sandbox.py`). Every size the code
  uses is a named value. A separate trusted process measures the solid; it is
  never scaled or fitted. A measured mismatch is a FAIL, and failed CAD is a
  placeholder that can never PASS.
- **No hand-modelled object in the registry.** The six v2 fixture generators
  (`LEGACY_FIXTURE_REGISTRY`) exist only to import archived v2 records.

This is the answer to "what's actually hard here?" If it erodes, the project is a diorama.

**Never invent a dimension, label, product or approval.** Drawing labels go into
`source_label` verbatim, or `null`. `ProductRef.ref` stays `null` until a real
manufacturer system is selected. A spec's `origin` must say how it was made
(e.g. `human-transcription-v1`). Never present a seed as model output.

## Out of scope — deliberately cut, do not reintroduce

- **No pixel-to-millimetre measurement.** No scale references, no tape-measure detection,
  no homography. This was the largest technical risk and it was cut on purpose.
- **No compliance verdict on real installations.** The tool shows what correct looks like.
  It does not grade the user's work. This avoids liability and removes the need for
  inspector validation.
- No IFC/BIM export, accounts, catalogues or native mobile.

If asked to add any of the above, push back and cite this section.

## Training and self-hosted models: allowed

User decision, 29 Oct 2026 (replaces the earlier "no model training" and "no
self-hosted LLM" cuts). Fine-tuning, CPT, SFT and RL, and self-hosted models are
in scope, run as Modal GPU jobs (`training/`). Use the cheapest GPU that fits
each job, judged by cost per job rather than price per hour; B200s only when a
job needs them (user decision, 30 Oct 2026). Train only on data we own (the
owner's own drawings) or generate (synthetic details from our programs). Never
train on manufacturer documents or standards text unless their licence allows
it. Datasets and weights stay out of this public repo. Spec:
`docs/DETAIL_WORLD_MODEL_SPEC.md`.

## Source documents — hard rule

**Never commit the documents the rules come from.** No PDFs, no verbatim clause text,
no screenshots of manual pages, no bulk extracted text. The repo is public and that
material is ABCB / Standards Australia / manufacturer copyright. `*.pdf` and
`data/**` are gitignored. Tools that write extracted text must write under `data/`.

Paraphrase and cite instead: a short one-sentence restatement in `text`, plus a
`source` resolving to a public URL. Short factual drawing labels quoted in
`source_label` / `source_quote` are fine. Never invent a number or a clause
reference. If it cannot be confirmed from the source, leave it out and flag it.
A wrong dimension in a construction tool is worse than a missing one.

Full rule: `.claude/rules/source-documents.md`

## Commands

Run from repo root, venv activated (`source .venv/bin/activate`):

```sh
pip install -r requirements.txt
python -m pytest tests/ -q                                   # no network needed (Muse, SAM, Modal stubbed)
python -m pytest tests/test_verify.py::test_d4_contacts_bear -q   # single test
scripts/test_postgres.sh                                     # jobs + migrations on Postgres (SETOUT_TEST_DATABASE_URL)
SETOUT_VAR=var python -m uvicorn app.main:app --port 8000    # API + web/dist at /, with an embedded worker
SETOUT_WORKER=external python -m app.worker                  # or run workers separately (same SETOUT_VAR / DB)
python -m app.versioning --dry-run                           # what an old var/library would import
python -m eval.agent_eval --own-drawings --var var/eval-x    # v3 evaluation in its own store

cd web && npm install
npm run build        # tsc -b && vite build -> web/dist (the server serves this)
npm run dev          # Vite dev server, /api proxied to :8000
npm test             # vitest run
npx vitest run src/provenance.test.ts   # single test file
npx tsc -b           # type-check
npm run lint         # oxlint

python e2e/drive.py [base-url]   # Playwright acceptance pass
```

Drawing segmentation (datasets, weights and pages stay under gitignored `data/`):

```sh
python tools/make_seg_dataset.py --out data/datasets/<name> --drawings 1000 --seed 1   # synthetic dataset
modal run training/rfdetr_seg.py --dataset <name>   # train, predict, score (spends GPU money)
python dashboard/build.py [--watch]                 # results page: data/dashboard/index.html
```

`e2e/drive.py` needs the server running, Playwright, system Google Chrome (macOS
path is hard-coded) and the reference PDF at `/tmp/conpro_sills.pdf`, which is not
committed. It drives the upload screen at `#/workbench`. Screenshots go to
`/tmp/setout-drive/`.

Web routes: `#/` library · `#/d/<detail_id>[?part=<id>]` detail page + AI guide ·
`#/workbench` add a detail from a PDF.

AI features need `META_API_KEY` in `.env`. CAD objects also need a Modal login
(`.venv/bin/modal token new`); without it they stay labelled placeholders that
FAIL. Server-side renders need Chrome (macOS) or `python -m playwright install
--with-deps chromium` (Linux), and `SETOUT_RENDER_URL` pointing at the API.

## Architecture

Two kinds of detail. **Seeds** (`app/seed.py` `SEEDS`) are hand-transcribed
reference specs served and corrected from code. **Everything AI-made** is an
immutable version in the database, changed only through jobs and reviews. An
old file library (`var/library/*.json`) is imported once at startup
(`versioning.import_library`); a seed imported earlier never shadows its code.

```
POST /api/documents  -> app/store.py: sha256, embedded raster figures (pypdf+pillow) -> var/docs/{id}/
GET/PATCH /api/specs -> seeds (PATCH only) or the detail's current database version
POST /api/builds     -> build_assembly (geometry.py) -> verify_all (verify.py), or a version's v3 verification
                        -> var/builds/{id}/{spec.json, scene.json, model.glb, report.json | error.txt}
```

Database and jobs (`app/database.py` tables, `migrations/` Alembic, `app/repository.py`):
SQLite at `SETOUT_VAR/v3.db` by default, Postgres via `SETOUT_DATABASE_URL`.
Bytes (crops, PDFs, renders, CAD, scenes) are content-addressed under
`SETOUT_ARTIFACT_ROOT` (default `SETOUT_VAR/artifacts`).

```
POST /api/jobs {kind, payload}   v3_api.submit validates, enqueues; kinds: generate_detail, revise_detail,
                                 cad_component, render, locate, batch_eval
app/worker.py                    leases a job (60 s, heartbeat 15 s, fencing, retry with backoff)
  -> app/agent.py                Muse tool loop; every model and tool step checkpointed in agent_steps,
                                 the conversation rebuilt from them on resume; budgets per run
  -> app/agent_tools.py          read_drawing · recall · write_program / patch_program · model_component /
                                 reuse_component · build_and_verify · render · critic · locate_on_drawing ·
                                 remember · finish
GET  /api/jobs?detail_id=…&active=1 · /api/jobs/{id} · /events (SSE) · /api/runs/{id}/steps (compact; ?full=1 raw)
GET  /api/details/{id}/versions · /api/versions/{id}[/scene|/reviews] · /api/artifacts/{id}
POST /api/versions/{id}/reviews  accept / correct / resolve / activate_memory · /api/details/{id}/publish
GET/POST /api/memories           lessons (proposed until a reviewer activates them)
```

A run that spends its budget while its draft still has FAILs (e.g. CAD
placeholders) and made progress continues automatically as a `revise_detail`
job, at most `SETOUT_AUTO_CONTINUE` times (default 2; `0` turns it off). The
finished job records `continued_by`, and the workbench, detail page and trace
follow the chain. A page reload picks a running job back up through
`/api/jobs?detail_id=…&active=1`.

The older routes stay for the UI and map onto jobs: `POST /api/generate`,
`/api/specs/{id}/critique|cad|locate` enqueue jobs and are polled with
`GET /api/generate/{job_id}`. Only the guide is a short in-process request
(`GEN_JOBS`). `#/render/<version_id>` is the headless render page
(`app/render.py`), `#/jobs/<job_id>` a run trace.

Cross-file contracts. These are not obvious from any single file:

- **Gates are the honesty boundary** (`generate.gate`, run inside
  `native_program.prepare` on every write and patch). A `drawing` param survives
  only if its quote matches a READ dimension and the number appears in the quote;
  labels must be verbatim READ labels; anything else is demoted to an assumption
  with a note. An agent can never create `user_input` or `manufacturer_doc`
  values. Never loosen this to make a demo look better.
- **Roles come from the drawing, not our vocabulary.** The READ classifies each
  label as `layer`, `object`, `zone` or `other`; `check_roles` makes a part
  carrying that label take the matching role. Objects declare `form` (`shaped` /
  `block`), and the verifier's form check reads the declaration. SAM chooses its
  prompts by role too. Never key a check off part names: the engine is
  trade-agnostic.
- **Zones are drawn, not built** (clearances, circulation spaces, set-out lines,
  datums): role `zone`, an exact primitive compiled with representation `zone`,
  shown see-through, with no install step and no physical relations. They may
  carry extent features, so a printed clearance can still be measured.
- **Labels are a choice, not free text.** The authoring schema
  (`program_schema(labels=...)`) offers only the READ's labels or null;
  `label-coverage` lists layer/object labels no part carries.
- **Program grammar ↔ schema ↔ verifier:** a new shape needs `SHAPE_DIMS` and
  `GENERATOR` (`app/program.py`), a generator in `REGISTRY`, the `NativePart`
  shape literal (`app/native_program.py`, which feeds `program_schema`), the
  agent instructions in `app/agent.py`, and a bounds check in `verify_program_dims`.
- **Completion is decided in code** (`repository.completion_errors`): `finish(done)`
  needs the exact version's verification with no FAIL or required UNVERIFIED, a
  passing independent critic on that version and manifest, and readable evidence.
  Publishing also needs a named reviewer's acceptance.
- **Evidence is pinned to `VERIFIER_VERSION`** (a hash of the verifier code).
  Changing the verifier invalidates earlier verifications; they are recomputed.
- **Guide grounding** (`guide.ungrounded`): every number spoken must be a param
  value, a drawing value, a number in a quoted label, or a step number. One retry,
  then flagged in the UI. Action ids are filtered to real part ids.

- **Adding a seed:** write a seed function in `app/seed.py` and add it to `SEEDS`.
- **Adding a generator:** add it to `REGISTRY` in `app/geometry.py`. Components must
  reference `GEN_VERSION` (`setout-mesh-v0`) exactly, or the build fails.
- **Schema changes need a migration** in `migrations/versions/`;
  `tests/test_migration_v3.py` fails when `app/database.py` and the migrations differ.
- **Verification keys off specific component ids and param names**
  (`app/verify.py`): `slab_upper` / `slab_lower`.`step_height`, `sealant`.`width`,
  `screed`.`fall_ratio`. A `step_height` note starting with `MINIMUM` makes the
  check one-sided. Contact checks are declared per detail in
  `spec.context["contact_checks"]` as `[top_id, bottom_id, description]`. A drawing
  param with no dedicated check is reported UNVERIFIED, never silently passed.
- **The spec schema is mirrored by hand** in `web/src/types.ts`, and endpoints in
  `web/src/api.ts`. Change `app/spec.py` or `app/main.py` responses and you update both.
- **`e2e/drive.py` drives the UI by DOM ids/classes** carried over from the old vanilla
  viewer (`#pdf`, `#upload`, `.crop[data-crop=…]`, `#docline`, …). Renaming them in
  `web/src/App.tsx` breaks the acceptance pass.
- **`PATCH /api/specs/{id}`** corrects seeds only: edits become `user_input`
  provenance (overriding a drawing value is tagged in the note) in
  `var/spec-{id}.json`. Delete it to get back to the seed. AI details are corrected
  in Review, and each correction is a new version with proposed lessons.
- Runtime state lives under `SETOUT_VAR` (default `var/`, gitignored).
  `tests/conftest.py` points it, the database and the artifact store at a temp dir.

## Conventions

- **Millimetres everywhere** in specs and rules. The viewer scales by 0.01 at render
  time only (`app/units.py` `MM_PER_VIEW_UNIT` == `K` in `web/src/viewport.ts`). GLB is
  SI metres. Conversions are tested in `tests/test_units.py`.
- **Origin:** interior FFL on the step face. `+x` toward the exterior, `+y` up,
  `+z` along the sill (an illustrative extent: an assumption, excluded from accuracy checks).
- Component ids must stay stable. The viewer, GLB nodes and report all key off them.
- Meta API: `META_API_KEY` in `.env` (copy `.env.example`). The model is
  `MODEL_API_MODEL`, else `META_MODEL` (default muse-spark-1.3). The playground
  builds and verifies without a key. The SAM 3.1 call shape was verified 27 Oct (`/responses`,
  no separate endpoint). Re-check https://dev.meta.ai/docs/ before spending credits.

## Submission

Public repo, Apache-2.0 (matches `LICENSE`), **own work only**. The hackathon
requires the entrant to own everything submitted. Never show manual pages on
screen in any video.
