# Setout

**See how it should be built, before you build it.**

A playground for manufacturers' construction installation details. A 2D
technical drawing becomes a validated component specification, which drives
dimension-based 3D components, assembly, installation sequence, and a
verification report — every value traceable to its source.

Meta Global AI Developer Hackathon 2026 · Indie Developer Track · Build for Your World
Submit by **Wed 4 Nov 9:00 PM ET** = **Thu 5 Nov 1:00 PM Sydney**

We start with door-sill waterproofing because the details are published,
dimensioned, and drawn — and because waterproofing is the most common
serious defect in NSW apartments (a NSW Government study of 492 strata
buildings found ~39% with serious defects, 63% of those waterproofing).
The engine is trade-agnostic: any installation detail can become a
playground spec. Point your phone at a junction; the detail appears in 3D
with its governing evidence attached.

---

## 1. Quickstart — door-sill playground

The primary track: upload → detail crop → validated spec → generated
assembly → viewer with source evidence and a verification report.

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q
cd web && npm install && npm run build && cd ..
SETOUT_VAR=var python -m uvicorn app.main:app --port 8000
```

Then open http://localhost:8000 in a browser: upload the reference PDF
(Conpro door/window-sills sheet), pick a detail variant, Generate, and
inspect. No API key is needed for this flow — the seed specs are
human-transcribed and honestly labelled as such.

Two documented variants ship, both from the same reference sheet:

| Variant | Detail | Key drawing dims | Fixing concept |
|---|---|---|---|
| D3 `conpro-door-sill-D3` | Standard sill section, door opening | 100 mm-min step, 6 mm gap, 1:80 fall | Packer-bedded frame |
| D4 `conpro-subsill-D4` | Sub-sill fixing in slab set-down | 80 mm set-down | Booker rod + grout + CFC packers, back nuts/washers |

Switching variants rebuilds genuinely different components, parameters,
and install orders through the same spec → generate → verify pipeline —
never a rename.

## 2. What the viewer does

- Source PDF upload with checksum; embedded drawing figures extracted
  losslessly and listed as selectable crops.
- Label overlay on the crop (indicative regions, honestly labelled —
  not SAM masks yet).
- Component list with provenance badges: blue = drawing value, amber =
  assumption, teal = user input, grey = mixed.
- One-click evidence from the list, the crop overlay, or the 3D view:
  exact drawing label (or "none — not invented"), role, parameters with
  per-value provenance and source quotes, product status (null until a
  system is selected — no claims made), relations, install position.
- Layer show/hide, orbit/zoom, exploded view, section clip, and
  installation-sequence playback.
- Parameter editing with rebuild: assumption values editable directly;
  drawing values locked unless explicitly overridden (recorded as a user
  override, never silent).
- Verification panel: every check PASS / FAIL / UNVERIFIED / N-A with
  expected vs actual numbers; unresolved items listed with build impact.
- Artifact downloads: `spec.json`, `scene.json`, `model.glb`, `report.json`.

## 3. How it works

```
PDF upload -> embedded figures -> canonical spec v1 (Pydantic)
  -> closed-set mesh generators -> triangle meshes + binary GLB
  -> deterministic verification -> viewer + report
```

- **Canonical spec** (`app/spec.py`, seeds in `app/seed.py`): versioned
  schema with source checksum, crop identity, millimetre units, exact
  drawing labels, per-parameter provenance (drawing / manufacturer doc /
  user input / assumption), typed assembly relations, install
  dependencies (cycle-checked), unresolved list, and verification block.
- **Generators** (`app/geometry.py`): closed set — box, L-angle,
  section-following sheet, bead, rod, vertical post, fillet, wedge.
  Membranes follow the section profile (upstands, faces, runs); the
  fillet is a true triangular prism; the screed falls at the drawn
  ratio. No free-form geometry code anywhere in the workflow.
- **Verification** (`app/verify.py`): per-parameter tolerances (minimum
  vs exact), triangle-surface contact-gap measurement, mesh and
  structure checks. Assumptions and unresolved items can never produce
  a verified PASS.
- **Jobs** (`app/main.py`): background builds with real states
  (queued → running → done/failed), progress stages, and preserved
  failure logs. SQLite + filesystem storage under `var/`.
- **Evaluation** (`docs/EVAL.md`): adapted from MUSE v3
  (arXiv:2605.28579v3, repo HEAD `dcb1638`), stages A/B/C with
  explicit adaptation notes. Latest: D3 PASS 0 FAIL, D4 28 PASS 0 FAIL.

Units: millimetres in the spec, ×0.01 in the viewer, SI metres in GLB
(all conversions tested).

## 4. Repository layout

```
app/                  FastAPI backend: spec, seeds, generators, GLB export,
                      verification, document store, job builds.
web/                  React + TypeScript viewer (Vite, Three.js 0.149 npm).
                      `npm run build` produces web/dist, served at /.
e2e/drive.py          Headless browser acceptance pass (both variants).
tests/                29 pytest: spec validation, units, geometry dims,
                      contacts, API end-to-end.
docs/                 GAP_ANALYSIS (audit), PLAN, STATUS (running log),
                      EVAL, ARCH, DEMO (3-min script), FRONTEND_MIGRATION.
spec/SPEC.md          Original plan, scoring, schedule, risks.
rules/rules.json      31 NCC rules as structured data (schema v1.0,
                      locked and validated). Values human-verified 28 Oct.
generator/            NCC parametric details: 3 shower/bathroom details,
                      every solid carrying its rule id, violation payloads.
extractor/            NCC rule extraction + schema validator + merge tool.
viewer/               NCC static viewers: index.html (desktop orbit),
                      jarvis.html (phone-first site view).
perception/           SAM 3.1 kill-check (re-verify endpoint before runs).
benchmark/            Cold vs +rules vs +retry, 31 questions,
                      muse-spark-1.3: cold 35%, grounded 100%, retry 100%.
data/                 Footage and manuals (gitignored).
```

## 5. Original track — NCC shower details

```bat
scripts\regen.bat
```

Then open `viewer\index.html` (works from `file://`). Violation demo:

```bat
scripts\regen.bat --violate membrane_return_onto_floor_mm=20
```

The membrane return shortens in the 3D *and* the check panel turns red —
because the geometry is built from the number, not drawn around it.

## 6. Development

```sh
python -m pytest tests/ -q          # backend suite (29 tests)
cd web && npm test                  # Vitest unit tests
cd web && npx tsc -b                # type-check
cd web && npm run dev               # Vite dev server, /api proxied
python e2e/drive.py                 # browser acceptance (needs server,
                                    # system Chrome, PDF at /tmp/*.pdf)
```

Conventions: millimetres everywhere; provenance on every important value;
exact drawing labels preserved verbatim, never invented; assumptions
visible and excluded from verification; no credentials outside `.env`
(gitignored) and managed secrets.

## 7. Known limitations

- Specs are human transcriptions (labelled); Spark/SAM interpretation,
  CadQuery solids, headless-Blender rendering, and Modal jobs are future
  milestones — see `docs/PLAN.md` and `docs/STATUS.md`.
- Product references are all null: no manufacturer system selected, and
  the Conpro sheet is treated as a reference drawing, not a product
  approval. No endorsement is implied.
- D1/D2 crops have no specs yet; interference checking is AABB-level;
  single sections cannot fix 3D depth (600 mm illustrative extent,
  excluded from accuracy checks).

## 8. The rules that protect the project

- **The 3D is GENERATED from source numbers. Never modelled by hand.**
- **Never invent a dimension, label, product, or approval.** Provenance
  or it didn't happen.
- **Never commit source documents.** No PDFs, verbatim clauses, or manual
  figures (short factual labels quoted with citation per the brief).
  Full rule: `.claude/rules/source-documents.md`.

## 9. Setup

```bat
copy .env.example .env
```

Fill in `META_API_KEY` from https://dev.meta.ai/ for extraction,
benchmark, and SAM runs. The playground builds and verifies without one.

## 10. Out of scope, deliberately

- No pixels-to-millimetres measurement of install photos
- No compliance verdict on real installs
- No model training (no LoRA, no GRPO)
- No IFC/BIM export, accounts, catalogues, native mobile, self-hosted LLM

## Licence

Apache-2.0 (see `LICENSE`). Your own work only — the hackathon requires you
to own everything you submit.
