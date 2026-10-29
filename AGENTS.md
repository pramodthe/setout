# AGENTS.md

Read `CLAUDE.md` first. It holds the project rules, the architecture and the
cross-file contracts. This file is the short version for any coding agent.

## Rules you must not break

- **Never commit source documents.** That means no PDFs, no verbatim clause
  text, and no screenshots or bulk extracted text. Tools write extracted text
  under `data/`, which is gitignored. Full rule:
  `.claude/rules/source-documents.md`.
- **Never invent a dimension, label, product or approval.** Copy drawing
  labels verbatim or leave them null. A value without a source quote is an
  `assumption`.
- **Keep the honesty gates** (`gate()` in `app/generate.py`, run on every
  program write in `app/native_program.py`). A `drawing` value needs its quote in
  the read pass, and its number must appear in that quote. Never loosen this.
- **3D comes from named values, never placed by hand.** Layers are exact
  primitives; objects are parametric CadQuery measured independently. See "The
  invariant" in `CLAUDE.md`.
- **Stay trade-agnostic.** No check, prompt or schema rule may key off part
  names such as a fixture or material. Roles come from the drawing reading;
  objects declare their form.
- **Follow the "Working rules" in `CLAUDE.md`.**

## Where the work is

- `docs/PLAN_V3.md`: the v3 design (agent harness, database and persistent
  jobs, memory, AI-written CAD objects, renderer and critic, frontend). It is
  built: `docs/V3_IMPLEMENTATION.md` records how, `docs/V3_EVAL.md` the
  measured results, including where v3 is still behind v2.
- `docs/MULTI_AGENT_SPEC.md`: the next step (a LangGraph team of specialist
  agents). Spec only; no code yet.
- `docs/STATUS.md`: the running log, newest first. Add an entry when a
  milestone lands.
- `docs/ARCH.md`, `docs/PLAN.md` and `docs/GAP_ANALYSIS.md` are historical:
  they describe the first vertical slice.

## Commands

```sh
python -m pytest tests/ -q          # backend tests; Muse, SAM and Modal are stubbed
SETOUT_VAR=var python -m uvicorn app.main:app --port 8000   # the app, with an embedded worker
SETOUT_WORKER=external python -m app.worker                 # separate workers (same SETOUT_VAR / DB)
cd web && npm install && npm run build   # viewer -> web/dist, served at /
cd web && npm test && npx tsc -b && npm run lint
python e2e/drive.py                 # browser acceptance pass (see CLAUDE.md)
scripts/regen.sh                    # NCC scenes (or scripts\regen.bat)
```

AI features need `META_API_KEY` in `.env` (copy `.env.example`). CAD parts also
need a Modal login (`modal token new`).

## Code map

- `app/main.py`: FastAPI routes for the UI (seeds, library, builds, previews,
  guide); `app/v3_api.py`: jobs, runs, versions, reviews, memories
- `app/database.py`, `migrations/`, `app/repository.py`, `app/artifacts.py`:
  schema, Alembic migrations, leases/budgets/checkpoints, content-addressed bytes
- `app/worker.py`, `app/agent.py`, `app/agent_tools.py`, `app/model_gateway.py`:
  persistent worker, the Muse tool loop, its tools, checkpointed model calls
- `app/generate.py`: the READ pass and the honesty gates;
  `app/native_program.py`, `app/program_schema.py`, `app/program_edits.py`:
  the v3 program, its wire schema, small pointer edits; `app/program.py`: the
  grammar and compiler
- `app/verification_v3.py`, `app/interfaces.py`, `app/verify.py`: measurement,
  assembly and source checks
- `app/cad_components.py`, `app/cad_sandbox.py`: AI-written CadQuery objects in
  the Modal sandbox, measured by a trusted process
- `app/render.py`: headless renders of the real viewer (`#/render/<version>`)
- `app/review_v3.py`, `app/lessons.py`: the review screen on versions; lessons
  a correction teaches
- `app/versioning.py`: figure registration, import of the old file library
- `app/geometry.py`: mesh generators (`REGISTRY`); `app/sam.py`: SAM 3.1 links
- `app/guide.py`, `app/library.py`: the AI guide, the library index
- `app/store.py`, `app/spec.py`, `app/seed.py`: uploads, the spec schema, the
  two hand-transcribed seeds
- `web/src/`: React + TypeScript viewer:
  - `viewport.ts` (three.js);
  - `App.tsx` (workbench);
  - `DetailView.tsx`, `Library.tsx`, `Guide.tsx`, `ReviewPanel.tsx`;
  - `JobPanel.tsx` (run trace), `VersionPanel.tsx`, `LessonsPanel.tsx`,
    `RenderView.tsx` (headless render page).
- `tests/`, `e2e/drive.py`: tests, browser acceptance pass
- `eval/agent_eval.py`, `eval/memory_eval.py`, `eval/v3_objects.py`,
  `eval/v3_smoke.py`: live evaluations and probes (they spend API credits)
- `scripts/demo_drawing.py`: draws our own test drawings into `data/demo/`
- `ncc/`: the original NCC track (Python stdlib only)
