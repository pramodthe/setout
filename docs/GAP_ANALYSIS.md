# Gap analysis — Setout vs construction-detail playground brief

Date: 2026-10-28. Method: read every source file, ran the generator path by
inspection, extracted the reference PDF, checked for backend calls.

> Layout note: this audit cites pre-restructure paths. The legacy NCC track
> (`rules/`, `generator/`, `extractor/`, `viewer/`, `perception/`,
> `benchmark/`, `spec/`) now lives under `ncc/` with identical contents;
> findings are unchanged.
Reference PDF: Conpro "Door and Window Sills Waterproofing" (2 pages, fetched
2026-10-28; page 1 = 4 door-sill drawings, page 2 = window sills). The PDF is
NOT committed (repo rule + `.gitignore` `*.pdf`).

## What genuinely works

- `generator/detail_generator.py` — parameter-driven geometry from
  `rules/rules.json`. Three details (`shower_hob`, `wall_floor_junction`,
  `internal_corner`); every solid carries `rule_id` provenance; violation
  overrides flip checks FAIL. Ran in CI-equivalent form: `viewer/scene.json`,
  `scenes.js`, `scenes_violate.js` are consistent with its output shape.
- `extractor/validate_rules.py` — schema lock v1.0 with self-test
  (`--self-test`, 12 negative fixtures). `extractor/merge.py` refuses bad
  candidates (non-mm units, dup pairs, bad text) and re-validates before write.
- `extractor/extract.py` + `benchmark/run.py` — real Meta Model API clients
  (`{META_API_BASE}/responses`, streamed SSE, `text.format json_schema`).
  Evidence they ran: `data/candidates.json` (24 candidates + 3 flags) and
  `benchmark/results.json` (muse-spark-1.3, n=31: cold 35.5%, grounded 100%,
  retry 100%). So Meta API access with `muse-spark-1.3` is verified.
- `viewer/index.html` — desktop orbit viewer: detail switcher, layer toggles,
  click-a-part provenance HUD, rule checks panel. Works from `file://`.
- `viewer/jarvis.html` — phone-first view: layer stepper, violation toggle,
  evidence PNG capture, loads user-supplied `killcheck_results.json` to place
  SAM boxes on a user photo. No backend calls (verified: no `fetch`/XHR in
  `viewer/`); all client-side file loads, which is honest for a static demo.

## What is incomplete or missing (vs the brief)

- No backend at all: no `app/`/`backend/`/`server.py`, no FastAPI, no jobs,
  no upload endpoint, no storage layer, no SQLite. Every UI action is local.
- No PDF→spec→assembly path: nothing ingests an uploaded drawing. The NCC
  rules in `rules/rules.json` are hand-verified code minimums, not components
  read from the Conpro drawing; no drawing labels, crops, masks, or overlays.
- No door-sill detail: existing details are shower/bathroom NCC details. No
  sill/subsill, waterstop, packer, sealant-bead, fillet, or flashing generators.
- No canonical component spec: no versioned schema with source checksum, crop
  transforms, provenance per parameter, install dependencies, unresolved list,
  or verification block. `rules.json` covers scalar minimums only.
- No CadQuery/Blender/STEP/GLB pipeline. Geometry is JSON boxes+polylines
  rendered as Three.js boxes (viewer `addBox`/`addPolyline`). Membrane is a
  chain of thin boxes — acceptable as a polyline extrusion, but there is no
  solid-validity, interference, or tolerance checking.
- No MUSE-style evaluation: benchmark measures Q&A exact-match only (stage A
  only, and on NCC text, not on drawing→spec recovery). No geometry/assembly
  checks, no per-parameter tolerances, no PASS/FAIL/UNVERIFIED report.
- No selector variation, no exploded/section view, no install-sequence
  playback, no parameter edit + rebuild, no artifact downloads.
- `perception/sam_killcheck.py` — client code is complete and careful, but its
  SAM output parsing was never exercised in this repo (no
  `killcheck_results.json` present; `data/frames/`, `data/videos/` empty).
  Endpoint shape per docstring verified against dev.meta.ai docs 27 Oct —
  re-verify before the demo run.
- `viewer/scenes_violate.js` violation builds are deliberate fixtures, clearly
  labelled — keep, but they are not evidence of interpretation accuracy.

## Mocks, stubs, placeholders (exact locations)

- `generator/detail_generator.py:88-93,233-238,299-303` — product dims
  (`hob_width=90`, `length=1200`, …) hard-coded with `rule_id: null` notes.
  Honest labelling (viewer shows "Product dimension"), but they are author
  choices, not sourced values. Same pattern must NOT silently carry over to
  door-sill dims: assumptions must be flagged, not footnoted.
- `viewer/index.html:109-143` — membrane rendered as rotated boxes; `coat`
  kind (`Primer`, `Bond breaker` in shower_hob) is silently skipped by the
  renderer (no `addCoat`), so two listed layers never draw. Real bug.
- No test suite: no `tests/`, no pytest config. Only `validate_rules.py
  --self-test` and `benchmark/run.py --dry-run` act as checks.

## Which outputs derive from the uploaded drawing

None. There is no upload, and no geometry traces to the Conpro PDF. The
closest provenance is NCC clause paraphrases in `rules.json`. Everything the
viewer shows derives from committed JSON + generator constants.

## Retain / repair / replace

- Retain: generator provenance pattern (`rule_id` on every solid), validator +
  merge gates, extractor/benchmark API client shape, both viewers as static
  fallbacks, `rules.json` NCC set (out of scope for door-sill, keep intact).
- Repair: viewer `coat` rendering (draw or remove), hard-coded product dims →
  explicit assumption records, jarvis demo-frame labelling.
- Replace/extend with new code (keep old paths working): new `app/` backend
  (FastAPI + SQLite + `var/` storage), canonical spec schema v1, door-sill
  component generators, mesh+GLB export, deterministic verification, new
  backend-wired viewer page, EVAL harness. CadQuery STEP + headless-Blender
  GLB/render land after the vertical slice (trade-off documented in STATUS).
