# Setout — Build Spec

**Meta Global AI Developer Hackathon 2026 — Indie Developer Track**
Theme: Build for Your World
Submission deadline: **Wed 4 Nov, 9:00 PM ET = Thu 5 Nov, 1:00 PM Sydney**

---

## One sentence

> See how it should be built, before you build it.

Point your phone at a junction. The manufacturer's installation detail appears in 3D,
with the governing clause attached.

**Scope note:** the engine is trade-agnostic. Any installation manual with numeric
rules can become a detail library. Wet-area waterproofing is simply the first one —
chosen because the defect data is damning and the rules are published and numeric.

---

## The problem (first library: wet-area waterproofing)

Waterproofing is the single most common serious defect in NSW apartments.

In a NSW Government study of 492 strata buildings completed after July 2014:
- ~**39%** had serious defects in common property
- of those affected buildings, **63%** had waterproofing defects
- across all buildings surveyed, waterproofing defects affected **23%**

Source: *Construct NSW — Serious defects in residential apartment buildings*, Sept 2021.

The rules exist. Every manufacturer publishes an installation manual, and the NCC
sets hard numbers. **The guides are boring, so nobody reads them on site.** Then the
tiles go on and nobody can see the membrane again until it leaks.

## Who it's for

The **waterproofer on site**, at membrane stage, before the tiler arrives.

### The framing that makes it a product

Do NOT pitch this as the builder checking up on the trade.

Pitch it as **the waterproofer's own evidence**. When a bathroom leaks three years
later, the waterproofer gets blamed and has nothing to prove they did it right. A
timestamped visual record of your own work, taken before anything covered it, is
worth money to *you*.

That makes it a tool tradies want, not one imposed on them. Say this in the video.

---

## What it does NOT do

Decided deliberately. Do not let scope creep put these back in.

- **No precise measurement.** No pixels-to-millimetres, no tape measure, no scale
  reference. This was the biggest technical risk and it is cut.
- **No compliance verdict on real work.** It does not say "your install fails".
  It shows what correct looks like. No liability, no inspector validation needed.
- **No model training.** No fine-tuning, no LoRA, no GRPO. See "Future work".

---

## Architecture — four layers

```
[ Manufacturer PDF + NCC ]
            |
            v
   (1) RULE EXTRACTION          Muse Wonder reads the manual,
            |                   emits rules.json with clause citations
            v
   (2) PARAMETRIC GENERATOR     code builds 3D geometry FROM the numbers
            |                   <-- THIS IS THE TECHNICAL CORE
            v
   (3) PERCEPTION               SAM 3.1 finds what the camera is looking at,
            |                   tracks it so the overlay stays anchored
            v
   (4) JARVIS VIEW              3D detail rendered over the live frame,
                                layer by layer, each surface citing its clause
```

### Layer 1 — Rule extraction

Muse Wonder (1M context, reads PDFs natively) ingests NCC 2022 Housing Provisions
Part 10.2 and 2–3 manufacturer manuals. Outputs structured rules.

Hard numbers already confirmed from NCC 2022 Part 10.2:

| Requirement | Value |
|---|---|
| Shower floor below FFL outside shower | >= 25 mm |
| Waterstop vertical leg above FFL (level-threshold shower w/ screen) | >= 5 mm |
| Wall/floor flashing vertical leg above FFL | >= 25 mm |
| Wall/floor flashing horizontal leg width | >= 50 mm |
| Vertical flashings above FFL | >= 1800 mm |
| Membrane up wall above finished tile level | >= 1800 mm |
| Membrane return onto floor from hob outside face | >= 50 mm |
| Internal flashing leg overlap | >= 40 mm |
| External flashing overlap with wall sheeting | >= 32 mm |

These are exactly the kind of numbers a generator can consume and a benchmark can test.

### Layer 2 — Parametric generator  ** THE CORE **

**The 3D must be GENERATED from the rules, never modelled by hand.**

If you hand-model five nice details in Blender, a judge asks "did you just model
these yourself?" and the project deflates in one question.

Instead: rules -> parameters -> code builds geometry. Feed it a new manual, get new
details automatically. Every surface carries the rule id that set it, so tapping the
membrane return shows the clause that made it 50 mm.

That is a system, not a diorama. It is also the answer to "what's actually hard here?"

### Layer 3 — Perception

SAM 3.1 via Meta Model API. Concept prompts, not training:
`"wall floor junction"`, `"floor waste"`, `"pipe penetration"`, `"shower hob"`,
`"internal corner"`.

One call returns detections, pixel masks and identity-preserving video tracks.
Its job is **"what am I looking at"** and **"keep the overlay anchored"** — not
"how big is it".

### Layer 4 — Jarvis view

Browser app. Video frame + SAM mask + generated 3D detail composited over it.
Layer-by-layer reveal (substrate -> primer -> bond breaker -> membrane -> screed).
Each layer annotated with its clause.

---

## The benchmark (keep this — it is where the rigour lives)

Your own observation: no LLM answers these accurately. Prove it.

60–100 questions drawn from NCC Part 10.2 and manufacturer manuals, with known
correct answers. Then three bars:

1. Muse Wonder cold (no manual)
2. Muse Wonder + rules.json in context
3. + verifier rejection and retry

That chart is your **Technical depth (20%)** score and it demonstrates why the
system needs grounded rules rather than a chatbot.

---

## How this scores

| Criterion | Weight | How it lands |
|---|---|---|
| Impact | 30% | #1 serious defect in NSW apartments. Built by someone who does the work. |
| Creative use of Meta tools | 25% | SAM 3.1 anchors the overlay; Muse Wonder extracts the rules. Neither is glue. |
| Demo (incl. video) | 25% | Phone points at junction -> 3D detail appears with the rule. ~8 seconds to land. |
| Technical depth | 20% | Parametric generator + benchmark + provenance to clause level. |

---

## Eight-day plan

| Day | Date | Work |
|---|---|---|
| 1 | Tue 28 Oct | **KILL-CHECKS ONLY.** (a) Generator: one detail from numbers. (b) SAM 3.1 on a real bathroom video. |
| 2 | Wed 29 Oct | Rule extraction from NCC. Lock rules.json schema. |
| 3 | Thu 30 Oct | Rule extraction from one manufacturer manual. Generator covers 3 details. |
| 4 | Fri 31 Oct | Viewer: layer-by-layer reveal + clause annotations. |
| 5 | Sat 1 Nov | Perception wired to viewer. Overlay anchored to SAM tracks. |
| 6 | Sun 2 Nov | Benchmark built and run. The three-bar chart. |
| 7 | Mon 3 Nov | Polish. Real site footage. Second detail set from a different manual. |
| 8 | Tue 4 Nov | **Video + submit.** Do not leave this to Wednesday. |

Reserve the whole of day 8. The Opus 4.7 "Most Creative" winner's single piece of
advice was that a 3-minute demo video takes far longer than anyone expects.

---

## Risks

| Risk | Mitigation | Decide by |
|---|---|---|
| SAM 3.1 can't see membrane edges (flat colour change on a flat plane) | Fall back to components it CAN see: floor waste, penetration, hob, screen track | Day 1 |
| No site access at membrane stage | Shoot your own mock-up, or use existing footage. Confirm Day 1. | Day 1 |
| Generator can't produce convincing 3D | Start with 2D cross-section extruded along one axis. Enough for the demo. | Day 1 |
| Scope creep back into measurement | Re-read "What it does NOT do" | Daily |

---

## Future work (say this, don't build it)

The verifier produces a reward signal: every run is a trajectory with a label
(`rules -> candidate detail -> verdict`). This week's work therefore generates the
dataset for RLVR/GRPO as a by-product, with no extra effort.

Extended from *checking rules* to *simulating outcomes* — where water gets in, where
the thermal bridge forms, where the fastener pulls out — the verifier becomes a world
model for building assemblies. Nobody has one.

Good closing slide. Not a week-1 build.

---

## Submission checklist

- [ ] Demo video (3 min). Shot on day 8.
- [ ] Public repo, Apache-2.0 (matches `LICENSE`), **your own work only**
- [ ] README with the NSW defect stat up top
- [ ] Benchmark chart
- [ ] Live demo link or hosted build
- [ ] Submitted by Tue 4 Nov night (deadline Thu 5 Nov 1 PM Sydney)
