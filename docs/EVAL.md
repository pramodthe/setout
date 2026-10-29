# EVAL.md — evaluation for the primary detail (D3)

## Reference (pinned)

- Paper: MUSE, "Benchmarking Assembly Structure and Interface Geometry in
  Multi-Component Text-to-CAD Generation", arXiv:2605.28579v3, 19 Oct 2026,
  CC BY 4.0. Fetched 2026-10-28 (HTML, 1,048,846 bytes).
- Repo: https://github.com/dong7313/muse, HEAD `dcb1638`
  (`dcb1638f556e2821170891ccfe744cffc5ac21d1`, 28 May 2026) at time of
  writing. No code vendored; protocol adapted, not claimed as parity.

MUSE evaluates text-to-CAD across four dimensions — code executability,
geometric validity, structural/interface fidelity, functional consistency —
with Structure and Interface Evaluation (SIE): a reference Physical Assembly
Graph G=(V,E), node labels l_i, edges with interface type t_ij and reference
clearance D*_ij plus allowable range [L_ij, U_ij], checked against the
generated B-Rep (component correspondence, connectivity, interface types,
clearances), plus a VLM judge with human-alignment reporting.

## Adaptations (explicit)

| MUSE | This project (v0) | Gap |
|---|---|---|
| Design Specification → CAD script | Drawing crop → canonical spec v1 → closed-set generators (no free code) | No LLM geometry code; executability = build done/failed |
| Physical Assembly Graph, typed edges + clearance ranges | `relations[]` (7 typed kinds) + per-param `tolerance_mm`; contact checks with documented ±2.0mm eval rule | Triangle-level interference deferred → `B-interference` UNVERIFIED |
| B-Rep validity | Closed-triangle meshes; non-empty + bounds-measured dims | No OCCT solid-validity until CadQuery lands |
| VLM judge + human alignment | None in v0 | Supplementary judging explicitly not implemented yet |
| Multi-case benchmark | Primary detail D3 only | Extra cases only when genuinely working |

## Stages and checks (implemented in `app/verify.py`)

- Stage A (drawing understanding): covered by seed-transcription review +
  `C-labels` (exact-label coverage). Model-vs-reference scoring starts when
  Spark interpretation lands; reference annotations will be held out from
  candidate input.
- Stage B (geometry/assembly): `S-*` structure, `B-step/B-gap/B-fall`
  (drawing dims vs measured mesh dims, per-param tolerances),
  `B-mesh-*`, `B-contact-*` (footprint-sampled bearing gaps).
- Stage C (end-to-end): `C-source` (sha256 match to uploaded PDF),
  `C-labels`, `C-products`, `C-sequence`, `C-waterproof` (N/A by policy: no
  performance claim).

Statuses: PASS / FAIL / UNVERIFIED / N/A. Critical-unresolved-with-geometry
(`S-unblocked`) FAILs the build. Assumptions appear only in `B-assumptions`
(N/A) and can never PASS a source-accuracy check.

## Latest result — D3, real reference PDF (2026-10-28)

Run: `/tmp/e2e_real_pdf.py` (kept outside repo for re-run) against
`f1cc2f45…` (Conpro sheet): 7 figures extracted, D3 crop `p0_Image21`
matched, 17 components, 228 triangles, GLB 25,580 bytes.

Verdict: **PASS (with UNVERIFIED items)**, 0 FAIL.
UNVERIFIED: `B-interference` (deferred, documented), `C-products` (no
system selected — honest). `C-source` PASS (doc linked).
Full per-check report ships with every build as `report.json` and renders in
the viewer Verification panel.

## Second case — D4 sub-sill set-down (2026-10-28)

Same harness, same reference PDF: 14 components, verdict **PASS (with
UNVERIFIED items)**, 0 FAIL (28 PASS / 2 UNVERIFIED / 4 N-A in the
headless drive). D4-specific results: `B-step` PASS (80.0 == 80 exact),
`B-gap`/`B-fall` N/A with reasons (gap undimensioned, no screed),
5/5 declared contacts PASS with measured 0.0 gaps, `C-source` PASS.
This is the documented selector variation: a genuinely different
build-up evaluated by the same checks, not a renamed D3.

## Batch evaluation — AI generation on a 24-drawing guide (2026-10-29)

`eval/batch_eval.py` runs the whole AI pipeline (read → compose → gates → compile
→ build → optional CAD → verify) on every embedded drawing of one 22-page
wet-area waterproofing guide, in its own storage folder. The guide is a
third-party document: it is not committed, and only counts are reported here.
Model: `muse-spark-1.3`, 4 drawings in parallel.

| Measure | Result |
|---|---|
| Drawings generated and built | **24/24** (16 sections, 7 room isometrics, 1 other) |
| Verification FAILs | **0** across all 24 details |
| Printed values confirmed against the drawing | **86**, of which **55** drive geometry; 19 of 24 details have at least one |
| Distinct drawing labels used verbatim | **173 of 221** read (78%); 198 of 364 parts carry one |
| Values demoted / labels dropped by the gates | **0** |
| Time per drawing | median **111 s** (54–212 s) |
| Tokens | median ~29.6k per drawing, ~715k in total |
| Shapes chosen by the model | box 258, profile 34, sheet 23, wedge 11, post 9, fixture 9, fillet 8, rod 7, revolve 2, cad 2, sweep 1 |

**What this does and does not show.**

- It shows the pipeline is robust: every drawing produced a buildable,
  verifiable detail.
- It shows the honesty gates hold: no value was claimed from the drawing
  without its printed words and number.
- "Confirmed" means the independent read and compose passes agree on the printed
  words and number. It does **not** prove the value is attached to the right
  part. That is what the review screen is for: in one bathroom detail a single
  printed "50mm" drove four unrelated parts, and the review panel shows exactly
  that.
- Zero FAILs means each detail was built exactly as its program says. It is not
  a claim that the program matches the drawing; the self-check and the human
  review address that.
