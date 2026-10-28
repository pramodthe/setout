# Setout — project instructions

AR-style installation guide for construction details. Point a phone at a junction, see
the manufacturer's correct detail in 3D with the governing clause attached.

**The engine is trade-agnostic.** Wet-area waterproofing is the FIRST detail library,
not the product. Any installation manual with numeric rules can become a library —
cladding, flashing, anchors, insulation. Do not hard-code waterproofing assumptions
into the generator, the schema or the viewer.

Built for the **Meta Global AI Developer Hackathon 2026**, Indie Developer track.
**Submission deadline: Wed 4 Nov 9:00 PM ET = Thu 5 Nov 1:00 PM Sydney.** Submit Tuesday night.

Read `spec/SPEC.md` before proposing architecture changes. It contains the plan,
the judging criteria, the day-by-day schedule and the risk table.

## The invariant — do not break this

**3D geometry is GENERATED from rule numbers. Never modelled by hand.**

Every solid emitted by the generator must carry the `rule_id` that set its dimensions.
If a dimension has no governing rule, mark it a product dimension with `rule_id: null`
and say so. Feeding a new manual must produce new details with no code changes.

This is the answer to "what's actually hard here?" If it erodes, the project is a diorama.

## Out of scope — deliberately cut, do not reintroduce

- **No pixel-to-millimetre measurement.** No scale references, no tape-measure detection,
  no homography. This was the largest technical risk and it was cut on purpose.
- **No compliance verdict on real installations.** The tool shows what correct looks like.
  It does not grade the user's work. This avoids liability and removes the need for
  inspector validation.
- **No model training.** No fine-tuning, no LoRA, no GRPO, no RL. There is neither the
  compute budget (~$150 API + ~$225 partner credits, all inference) nor the time.
  RLVR is future work; the verifier generates the trajectories for it as a by-product.

If asked to add any of the above, push back and cite this section.

## Source documents — hard rule

**Never commit the documents the rules come from.** No PDFs, no verbatim clause text,
no screenshots of manual pages, no bulk extracted text. The repo is public and that
material is ABCB / Standards Australia / manufacturer copyright.

Paraphrase and cite instead: a short one-sentence restatement in `text`, plus a
`source` resolving to a public URL. Never invent a number or a clause reference — if
it cannot be confirmed from the source, leave it out and flag it. A wrong dimension
in a construction tool is worse than a missing one.

Full rule: `.claude/rules/source-documents.md`

## Layout

```
spec/SPEC.md          The plan. Authoritative.
rules/rules.json      12 NCC rules as structured data. The schema the extractor
                      must produce. 9 of them drive geometry.
generator/            THE CORE. detail_generator.py turns rules into 3D.
viewer/               Three.js viewer. index.html + generated scene.js/scene.json.
perception/           SAM 3.1 kill-check. Endpoint shape is UNVERIFIED.
benchmark/            12 seed questions, target 60-100.
data/                 Footage and manuals. Gitignored.
```

## Commands

```bat
scripts\regen.bat                                          rebuild scene from rules
scripts\regen.bat --violate membrane_return_onto_floor_mm=20   demo a failing rule
```
Then open `viewer\index.html` directly — it loads `scene.js` via `<script>`, so it works
from `file://` with no server. If you switch the viewer to `fetch('scene.json')` you will
break that; run `scripts\run_viewer.bat` instead.

## Conventions

- **Millimetres everywhere.** The viewer scales by 0.01 at render time only.
- **Origin** at the outside face of the hob, at finished floor level.
  `+x` away from the shower, `+y` up, `+z` along the hob.
- Generator uses the **Python standard library only**. Keep it that way — it has to run
  anywhere with no install step.
- Rule ids look like `NCC-10.2-hob-membrane-return`. Keep them stable; the viewer and
  benchmark both key off them.
- Every rule needs a verifiable `source` and real clause text. **Never invent a number
  or a clause.** If a value can't be confirmed, leave it out and flag it.

## State as of 27 Oct

Done: spec, rule set, generator (passing, with violation demo), viewer, kill-check
script, benchmark seed.

Next, in order:
1. **Day 1 kill-check** — run `perception/sam_killcheck.py` on real bathroom footage.
   **Verify the SAM 3.1 endpoint against https://dev.meta.ai/docs/ first** — the shape
   in that file is a placeholder.
2. **Rule extraction** — Muse Wonder reads NCC Part 10.2 and manufacturer PDFs, emits
   more entries matching the existing `rules.json` schema.
3. Second and third details (wall/floor junction, pipe penetration).
4. Wire SAM tracks to the viewer so the overlay stays anchored.
5. Benchmark: three bars — cold, with rules in context, with verifier retry.

## Hard dependency

Real footage of a bathroom at membrane stage. Without it this is a toy. If it hasn't
been confirmed, raise it before building anything that assumes it.

## Submission

Public repo, Apache-2.0 (matches `LICENSE`), **own work only** — the
hackathon requires the entrant to own everything submitted. Reserve all of
day 8 for the demo video.
