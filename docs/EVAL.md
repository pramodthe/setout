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
