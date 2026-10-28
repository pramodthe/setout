# Setout

**See how it should be built, before you build it.**

A playground for manufacturers' construction installation details: a 2D
technical drawing becomes a validated component specification, which drives
dimension-based 3D components, assembly, install sequence, and a
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

## Track 1 · Door-sill playground (current work)

Upload → detail crop → validated spec → generated assembly → viewer with
source evidence and a verification report. Two documented variants from the
Conpro door-sills sheet: **D3** (standard sill, 100 mm-min step,
packer-bedded) and **D4** (rod-fixed sub-sill in an 80 mm set-down).

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q
SETOUT_VAR=var python -m uvicorn app.main:app --port 8000
```

Then open http://localhost:8000 in a browser: upload the reference PDF,
pick a variant, Generate, and inspect. The viewer supports orbit, layer
toggles, click-for-evidence (list, crop overlay, or 3D), exploded view,
section clip, install-sequence playback, parameter edit + rebuild,
artifact downloads, and the verification panel.

Docs: `docs/GAP_ANALYSIS.md` (repo audit), `docs/PLAN.md`,
`docs/STATUS.md` (running log), `docs/EVAL.md` (MUSE-v3-based evaluation),
`docs/ARCH.md`, `docs/DEMO.md` (3-minute script).

## Track 2 · NCC shower details (original)

```bat
scripts\regen.bat
```

Then open `viewer\index.html` in your browser. Drag to orbit, click any part.

See a rule being violated:

```bat
scripts\regen.bat --violate membrane_return_onto_floor_mm=20
```

The membrane return shortens in the 3D *and* the check panel turns red —
because the geometry is built from the number, not drawn around it.

## What's here

```
app/                  Door-sill playground backend (FastAPI + Pydantic).
                      Canonical spec v1, closed-set mesh generators, GLB
                      export, deterministic verification, wired viewer.
                      See docs/ARCH.md.
tests/                29 pytest: spec validation, units, geometry dims,
                      contacts, API end-to-end incl. real-PDF figures.
docs/                 GAP_ANALYSIS, PLAN, STATUS, EVAL, ARCH, DEMO.
spec/SPEC.md          Original plan, scoring, schedule, risks.
rules/rules.json      31 NCC rules as structured data (schema v1.0,
                      locked and validated). Values human-verified 28 Oct.
generator/            NCC parametric details. Every solid carries the
                      rule id that set it. 3 details + violation payloads.
extractor/            Rule extraction + schema validator + merge tool
                      for second manuals. Stdlib only.
viewer/index.html     Desktop orbit view for NCC details (file:// OK).
viewer/jarvis.html    Phone-first view: site photo + SAM boxes, layer
                      stepper, violation toggle, evidence capture.
perception/           SAM 3.1 kill-check (endpoint shape per dev.meta.ai
                      docs; rerun before trusting a new verdict).
benchmark/            Cold vs +rules vs +retry runner, 31 questions,
                      muse-spark-1.3: cold 35%, grounded 100%, retry 100%.
data/                 Your footage and manuals (gitignored).
```

## The rules that protect the project

- **The 3D is GENERATED from source numbers. Never modelled by hand.**
  Hand-model the details and a judge asks "did you just model these
  yourself?" — generate them, and a new detail produces a new assembly.
- **Never invent a dimension, label, product, or approval.** A value comes
  from the drawing, a manufacturer document, an explicit user input, or a
  documented modelling assumption — and assumptions never count as verified.
- **Never commit source documents.** No PDFs, no verbatim clauses, no manual
  figures. Short factual labels are quoted with citation per the brief;
  bulk text stays out. Full rule: `.claude/rules/source-documents.md`.

## Setup

```bat
copy .env.example .env
```

Fill in `META_API_KEY` from https://dev.meta.ai/. The playground runs
without a key (seed specs are labelled human transcription); the key is
needed for extraction, benchmark runs, and SAM checks.

## Out of scope, deliberately

- No pixels-to-millimetres measurement of install photos
- No compliance verdict on real installs
- No model training (no LoRA, no GRPO)
- No IFC/BIM export, accounts, catalogues, native mobile, self-hosted LLM

## Licence

Apache-2.0 (see `LICENSE`). Your own work only — the hackathon requires you
to own everything you submit.
