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
- **Keep the honesty gates** (`gate()` in `app/generate.py`). A `drawing` value
  needs its quote in the read pass, and its number must appear in that quote.
  Never loosen this.
- **3D comes from named values, never placed by hand.** See "The invariant" in
  `CLAUDE.md`. `docs/PLAN_V3.md` proposes changing it, and that change needs
  the owner's sign-off.
- **Follow the "Working rules" in `CLAUDE.md`.**

## Where the work is

- `docs/PLAN_V3.md`: the target architecture and the six workstreams:
  - agent harness;
  - database and persistent jobs;
  - memory;
  - AI-written CAD objects;
  - the renderer and critic;
  - frontend.

  Items marked **[decide]** need the owner's sign-off before work starts on
  them.
- `docs/STATUS.md`: the running log, newest first. Add an entry when a
  milestone lands.
- `docs/ARCH.md`, `docs/PLAN.md` and `docs/GAP_ANALYSIS.md` are historical:
  they describe the first vertical slice.

## Commands

```sh
python -m pytest tests/ -q          # backend tests; Muse, SAM and Modal are stubbed
SETOUT_VAR=var python -m uvicorn app.main:app --port 8000   # the app
cd web && npm install && npm run build   # viewer -> web/dist, served at /
cd web && npm test && npx tsc -b && npm run lint
python e2e/drive.py                 # browser acceptance pass (see CLAUDE.md)
scripts/regen.sh                    # NCC scenes (or scripts\regen.bat)
```

AI features need `META_API_KEY` in `.env` (copy `.env.example`). CAD parts also
need a Modal login (`modal token new`).

## Code map

- `app/main.py`: FastAPI routes and in-process jobs
- `app/generate.py`: the AI pipeline (read, compose, gates, build, CAD, SAM)
  and the self-check
- `app/program.py`: the detail-program grammar and its compiler
- `app/geometry.py`: mesh generators (`REGISTRY`)
- `app/verify.py`: verification checks
- `app/meta_client.py`: Meta Muse API client
- `app/cad_sandbox.py`: CadQuery in a Modal sandbox
- `app/sam.py`: SAM 3.1 links between parts and the drawing
- `app/guide.py`, `app/review.py`, `app/library.py`: the AI guide, review and
  publish, the library index
- `app/store.py`, `app/spec.py`, `app/seed.py`: uploads and SQLite, the spec
  schema, the two hand-transcribed seeds
- `web/src/`: React + TypeScript viewer:
  - `viewport.ts` (three.js);
  - `App.tsx` (workbench);
  - `DetailView.tsx`, `Library.tsx`, `Guide.tsx`, `ReviewPanel.tsx`.
- `tests/`, `e2e/drive.py`, `eval/batch_eval.py`: tests, acceptance pass, batch
  evaluation
- `scripts/demo_drawing.py`: draws our own test drawings into `data/demo/`
- `ncc/`: the original NCC track (Python stdlib only)
