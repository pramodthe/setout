# Setout

**See how it should be built, before you build it.**

Point your phone at a junction. The manufacturer's installation detail appears in 3D,
with the governing clause attached.

*First detail library: bathroom waterproofing. The engine is trade-agnostic — any
manufacturer's installation guide can become a detail library.*

Meta Global AI Developer Hackathon 2026 · Indie Developer Track · Build for Your World
Submit by **Wed 4 Nov 9:00 PM ET** = **Thu 5 Nov 1:00 PM Sydney**

---

We start with wet-area waterproofing because it is the most common serious defect in
NSW apartments, and because the rules are published and numeric. The same pipeline
extends to cladding, flashing, anchors and anything else with an installation manual. A NSW Government
study of 492 strata buildings completed after July 2014 found about 39% had serious
common-property defects, and 63% of those affected buildings had waterproofing
defects. The rules exist — the guides are just too boring to read on site. Then the
tiles go on and nobody sees the membrane again until it leaks.

---

## START HERE

1. Read **`spec/SPEC.md`** — the whole plan, scoring, day-by-day, risks.
2. Run the generator and open the viewer (below). Two minutes.
3. Do **Day 1 kill-checks** tomorrow. Nothing else matters until they pass.

## Try it now

```bat
scripts\regen.bat
```
Then open `viewer\index.html` in your browser. Drag to orbit, click any part.

See a rule being violated:
```bat
scripts\regen.bat --violate membrane_return_onto_floor_mm=20
```
The membrane return shortens in the 3D *and* the check panel turns red — because
the geometry is built from the number, not drawn around it.

## What's here

```
spec/SPEC.md            The plan. Read this first.
rules/rules.json        31 NCC rules as structured data (schema v1.0,
                        locked and validated). Values human-verified 28 Oct.
generator/              THE TECHNICAL CORE. Builds 3D from rule numbers.
                        Every solid carries the rule id that set it.
extractor/              Rule extraction + schema validator. Stdlib only.
viewer/index.html       Three.js viewer. Layer toggles, clause on click.
perception/             Day 1 SAM 3.1 kill-check. Read the docstring.
benchmark/              Seeded with 12 questions. Grow to 60-100.
data/                   Your footage and manuals (gitignored).
```

## The one rule that protects the project

**The 3D is GENERATED from the rules. Never modelled by hand.**

Hand-model five details and a judge asks "did you just model these yourself?" and
the project deflates in one question. Generate them, and feeding a new manual
produces new details automatically. That is the answer to "what's actually hard here?"

## Out of scope, deliberately

- No pixels-to-millimetres measurement
- No compliance verdict on real installs
- No model training (no LoRA, no GRPO — see Future work in the spec)

## Day 1 (Tue 28 Oct) — kill-checks only

1. **Generator** — already passing. Confirm the viewer opens and the violation demo works.
2. **SAM 3.1** — film one real bathroom at membrane stage, run
   `perception/sam_killcheck.py`, read the verdict. **Verify the SAM endpoint
   against https://dev.meta.ai/docs/ before running** — the shape in that file is
   a placeholder.
3. **Confirm site access** this week at membrane stage. Without real footage this
   is a toy.

## Setup

```bat
copy .env.example .env
```
Fill in `META_API_KEY` from https://dev.meta.ai/. Python 3.10+ and a browser are
all you need; the generator uses only the standard library.

## Licence

MIT. Your own work only — the hackathon requires you to own everything you submit.
