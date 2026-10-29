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

## Two tracks in one repo

| | **Playground** (primary) | **NCC track** (original) |
|---|---|---|
| What | Any drawing in a PDF → AI-written detail program → meshes → verify → library, 3D viewer, AI guide | NCC Part 10.2 rules → shower details → static Three.js viewer |
| Code | `app/` (FastAPI), `web/` (React+TS), `tests/`, `e2e/` | `ncc/` (everything) |
| Source of numbers | `app/seed.py` (two hand-transcribed seeds) and AI-generated programs gated against the drawing (`var/library/`) | `ncc/rules/rules.json` (31 rules, 9 drive geometry) |
| Plan docs | `docs/PLAN_V3.md` (proposed: agent, database, jobs, memory), `docs/PLAYGROUND_PLAN.md`, `docs/STATUS.md` | `ncc/SPEC.md` |
| Deps | `requirements.txt` (pinned) | **Python stdlib only.** Keep it that way. |

Read the plan doc for a track before you propose architecture changes to it.
`docs/STATUS.md` is the running log, newest entry first. Check it for current
state and add an entry when a milestone lands.

## The invariant — do not break this

**3D geometry is GENERATED from source numbers. Never modelled by hand.**

- **Playground:** every mesh comes from a closed-set generator in
  `app/geometry.py` `REGISTRY`, driven only by spec `Param`s. Every param carries
  `provenance` (`drawing` / `manufacturer_doc` / `user_input` / `assumption`).
  Assumptions and unresolved items must **never** produce a verified PASS.
  The model writes *detail programs* (`app/program.py`: params plus parts whose
  dims are arithmetic expressions); our compiler builds the geometry.
- **The one exception: `cad` parts.** Model-written CadQuery runs only in a
  no-network Modal sandbox (`app/cad_sandbox.py`). It is fitted into an envelope,
  labelled "AI-modelled, illustrative", and never carries a verified dimension.
- **NCC:** every solid carries the `rule_id` that set its dimensions. A dimension
  with no governing rule is a product dimension with `rule_id: null`, and the
  code says so. Feeding a new manual must produce new details with no code changes.

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
- **No model training.** No fine-tuning, no LoRA, no GRPO, no RL. The compute
  budget (~$150 API + ~$225 partner credits) is for inference only.
  RLVR is future work; the verifier generates the trajectories for it as a by-product.
- No IFC/BIM export, accounts, catalogues, native mobile or self-hosted LLM.

If asked to add any of the above, push back and cite this section.

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

Playground (run from repo root, venv activated: `source .venv/bin/activate`):

```sh
pip install -r requirements.txt
python -m pytest tests/ -q                                   # no network needed
python -m pytest tests/test_verify.py::test_d4_contacts_bear -q   # single test
SETOUT_VAR=var python -m uvicorn app.main:app --port 8000    # serves API + web/dist at /

cd web && npm install
npm run build        # tsc -b && vite build -> web/dist (the server serves this)
npm run dev          # Vite dev server, /api proxied to :8000
npm test             # vitest run
npx vitest run src/provenance.test.ts   # single test file
npx tsc -b           # type-check
npm run lint         # oxlint

python e2e/drive.py [base-url]   # Playwright acceptance pass
```

`e2e/drive.py` needs the server running, Playwright, system Google Chrome (macOS
path is hard-coded) and the reference PDF at `/tmp/conpro_sills.pdf`, which is not
committed. It drives the upload screen at `#/workbench`. Screenshots go to
`/tmp/setout-drive/`.

Web routes: `#/` library · `#/d/<detail_id>[?part=<id>]` detail page + AI guide ·
`#/workbench` add a detail from a PDF.

AI features need `META_API_KEY` in `.env`. CAD parts also need a Modal login
(`.venv/bin/modal token new`); without it they fall back to boxes with a note.

NCC track:

```sh
scripts/regen.sh                                              # rebuild all NCC scenes from rules.json
scripts/regen.sh --violate membrane_return_onto_floor_mm=20   # demo a failing rule
python3 ncc/extractor/validate_rules.py                       # schema lock + benchmark cross-check
python3 ncc/extractor/validate_rules.py --self-test           # negative fixtures must all fail
python3 ncc/benchmark/run.py --dry-run                        # no API calls
```

(`scripts\regen.bat` on Windows.) Then open `ncc/viewer/index.html` (desktop) or
`jarvis.html` (phone-first) directly. They load `scene.js` / `scenes.js` via
`<script>`, so they work from `file://` with no server. Switching them to
`fetch('scene.json')` breaks that. Use `scripts/run_viewer.sh` if you need a server.

`regen.sh` overwrites the **tracked** `ncc/viewer/scene.json`, `scene.js` and
`scenes.js`. Re-run it without `--violate` before you commit.

## Playground architecture

```
POST /api/documents  -> app/store.py: sha256, embedded raster figures (pypdf+pillow) -> var/docs/{id}/
GET/PATCH /api/specs -> app/seed.py seed functions -> app/spec.py DetailSpec (Pydantic, validates refs + install cycles)
POST /api/builds     -> background thread: build_assembly (geometry.py) -> verify_all (verify.py)
                        -> var/builds/{id}/{spec.json, scene.json, model.glb, report.json | error.txt}
```

AI pipeline (`app/generate.py`, all calls through `app/meta_client.py`):

```
POST /api/generate {doc_id, crop_id} -> READ pass (labels, dims, type) -> COMPOSE pass
  (program JSON, strict schema) -> gate() -> compile_program -> build_assembly
  -> optional CAD parts in the Modal sandbox -> var/library/<detail_id>.json
POST /api/specs/{id}/critique {renders}  render -> compare -> correct, accepted only if
  quality() doesn't drop (failures, drawing values traced, labels)
POST /api/specs/{id}/cad {part_id}       re-model one part in CadQuery
POST /api/guide {detail_id, question}    tour of narration + viewer actions (app/guide.py)
GET  /api/library · /api/specs/{id}/scene · /api/specs/{id}/thumb (source drawing)
GET/POST /api/specs/{id}/preview          library card image: a render of the 3D, never the drawing
```

Long AI jobs share `GEN_JOBS` in `app/main.py` and are polled with
`GET /api/generate/{job_id}`.

Cross-file contracts. These are not obvious from any single file:

- **Gates are the honesty boundary** (`generate.gate`). A `drawing` param survives
  only if its `source_quote` appears in the READ and the number appears in the
  quote; labels must match READ labels verbatim; anything else is demoted to an
  assumption with a note. Never loosen this to make a demo look better.
- **Program grammar ↔ prompt ↔ schema:** a new shape needs `SHAPE_DIMS` and
  `GENERATOR` (`app/program.py`), a generator in `REGISTRY`, the enum and text in
  `PROGRAM_SCHEMA` and `GRAMMAR` (`app/generate.py`), and a bounds check in
  `verify_program_dims`.
- **Guide grounding** (`guide.ungrounded`): every number spoken must be a param
  value, a drawing value, a number in a quoted label, or a step number. One retry,
  then flagged in the UI. Action ids are filtered to real part ids.

- **Adding a detail variant:** write a seed function in `app/seed.py` and register it
  in `SPECS` in `app/main.py`.
- **Adding a generator:** add it to `REGISTRY` in `app/geometry.py`. Components must
  reference `GEN_VERSION` (`setout-mesh-v0`) exactly, or the build fails.
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
- **`PATCH /api/specs/{id}`** records edits as `user_input` provenance (overriding a
  drawing value is tagged in the note) and writes `var/spec-{id}.json`. Builds then
  use that file instead of the seed. Delete it to get back to the seed.
- Runtime state lives under `SETOUT_VAR` (default `var/`, gitignored). `tests/test_api.py`
  sets it to `/tmp/setout-test-var`.

## Conventions

- **Millimetres everywhere** in specs and rules. The viewer scales by 0.01 at render
  time only (`app/units.py` `MM_PER_VIEW_UNIT` == `K` in `web/src/viewport.ts`). GLB is
  SI metres. Conversions are tested in `tests/test_units.py`.
- **Origins differ by track:**
  - Playground: interior FFL on the step face. `+x` toward the exterior, `+y` up,
    `+z` along the sill (an illustrative extent: an assumption, excluded from accuracy checks).
  - NCC: outside face of the hob at finished floor level. `+x` away from the shower,
    `+y` up, `+z` along the hob.
- Component ids (playground) and rule ids (NCC, e.g. `NCC-10.2-hob-membrane-return`)
  must stay stable. The viewer, GLB nodes, report and benchmark all key off them.
- NCC rules pipeline: `ncc/extractor/extract.py` writes candidates only to
  `data/candidates.json` (it refuses other paths). A human confirms every number
  against the source. Then `merge.py --dry-run` / `--apply` assigns ids and
  re-validates. The model never assigns rule ids.
- Meta API: `META_API_KEY` in `.env` (copy `.env.example`). The model is
  `MODEL_API_MODEL`, else `META_MODEL` (default muse-spark-1.3). The playground
  builds and verifies without a key. The SAM 3.1 call shape was verified 27 Oct (`/responses`,
  no separate endpoint). Re-check https://dev.meta.ai/docs/ before spending credits.

## Hard dependency

The perception / AR-overlay work (`ncc/perception/`, SAM tracks anchoring the
overlay) needs real footage of a bathroom at membrane stage. Without it, that part
is a toy. If the footage hasn't been confirmed, raise it before building anything
that assumes it.

## Submission

Public repo, Apache-2.0 (matches `LICENSE`), **own work only**. The hackathon
requires the entrant to own everything submitted. Never show manual pages on
screen in any video (`docs/DEMO.md`, `ncc/VIDEO.md`).
