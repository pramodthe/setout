# Status — running log, newest first

## 2026-10-28 — audit + vertical slice build (in progress)

- Audited repo; wrote `docs/GAP_ANALYSIS.md`, `docs/PLAN.md`, this file.
- Retrieved Conpro reference PDF (2 pp; p.1 = 4 door-sill drawings). Read all
  four door-sill crops and their labels directly (embedded rasters via
  pypdf+pillow). Primary detail selected: D3 "STANDARD SILL SECTION DOOR
  OPENING".
- D3 source facts (exact drawing labels; nothing invented):
  dimensions: "6mm gap with backing rod and sealant", "100mm minimum step",
  "1:80 min. Fall to outlet". Components (labels quoted in seed spec):
  water-stop with membrane attached, membrane protection sheet, sill packer,
  decorative cover angle, drainage holes, waterproof membrane,
  slip/protection sheet, compressible perimeter joint, fillet or bond
  breaker, weep-hole, perimeter termination height (datum, no number).
- Trade-off invoked (brief §13 escape clause): v0 geometry = closed-set
  parameter-driven mesh generators + GLB export in pure Python (stdlib +
  numpy-free), driven by the canonical spec with stable IDs. CadQuery rigid
  solids + STEP measurement and headless-Blender sheets/render/GLB dock into
  the same spec as the next milestone; NOT silently substituted by browser
  boxes — v0 meshes carry per-vertex dims from spec params and are
  dimensionally verified against them. Reason: CadQuery+bpy install + Modal
  wiring inside MUST window risks the slice; mesh v0 keeps every MUST
  acceptance check (toggle, evidence, section match, verification) real.
- Meta API: access previously verified — `benchmark/results.json`
  (muse-spark-1.3, n=31, cold 35.5% / grounded 100% / retry 100%, 28 Oct).
  No new model calls spent during the slice build; seed spec is labelled
  human transcription, never presented as model output.

## 2026-10-28 (later) — vertical slice DONE

- Built `app/` backend (FastAPI, SQLite + `var/` storage): upload with
  sha256 + embedded-figure extraction, spec get/patch with USER_INPUT
  provenance, background job records (queued→running→done/failed),
  artifact download (spec/scene/model.glb/report).
- Canonical spec v1 (`app/spec.py`): versioned, ID refs, install-cycle
  check; D3 seed (`app/seed.py`): 17 components, exact drawing-label
  quotes, per-param provenance, 7 unresolved, 17 typed relations.
- Closed-set mesh generators (`app/geometry.py`): membrane/protection as
  section-following ribbons, triangular fillet, 6mm bead, 1:80 wedge;
  hand-rolled binary GLB export with stable node IDs.
- Deterministic verification (`app/verify.py`): per-param tolerances
  (100mm step min, 6mm gap, 1:80 fall), footprint-sampled contacts,
  PASS/FAIL/UNVERIFIED/N-A report.
- Viewer (`app/static/`): upload, crops, label overlay, component list
  with provenance badges, layer toggle, click evidence (drawing/crop/3D),
  explode, section clip, install playback, param edit + rebuild,
  verification + unresolved panels, downloads.
- E2E on the real reference PDF: 7 figures, D3 matched, 228 tris,
  verdict PASS with UNVERIFIED, 0 FAIL, C-source PASS. 22 pytest green.
- MUSE v3 pinned (paper 19 Oct 2026; repo HEAD `dcb1638`); `docs/EVAL.md`,
  `docs/ARCH.md`, `docs/DEMO.md` written.
- Headless browser drive PASSED (system Chrome via Playwright,
  screenshots read back): upload → D3 crop → build → orbit → layer
  toggle → evidence from list/crop/3D → explode → section → playback
  (mid-run step observed) → param edit + rebuild → 4 downloads →
  verification panel, 0 console/page errors. Drive script: /tmp
  (re-runnable); screenshots under /tmp/setout-drive/.
- Drive found + fixed: (1) sheet z-cap fan spanned concave folds —
  removed, open sheet edges documented, regression test
  test_sheet_tris_are_ribbon_only; (2) mixed-provenance badge mislabelled
  "assumption"; (3) rebuild with install-order on hid the scene;
  (4) favicon 404. Suite now 23 green.

## 2026-10-28 (later) — SHOULD selector variant DONE

- Second documented variant D4 "Sub-sill fixing in slab set down"
  (`door_subsill_d4`, 14 components): rod-fixed sub-sill in an 80mm
  set-down vs D3's packer-bedded sill at 100mm-min step. Different
  components (booker rod, grout, CFC packers, washer, nut, nut-patch
  membrane, sub-sill), different drawing dims (80 exact vs 100 min),
  different install order — selected in the viewer, rebuilt through the
  same canonical spec + job + verification path.
- Verification generalized: per-detail contact pairs from
  `spec.context["contact_checks"]`; B-step one-sided (MINIMUM) vs
  two-sided; B-gap/B-fall emit N/A when the detail lacks the feature.
- Contact sampling upgraded to triangle-surface (top-down footprint +
  bottom-up support centroids); was vertex-only and missed long quads
  and small supports. Covered by `tests/test_verify.py` (exact gaps).
- New `post` generator (vertical rod/post prism); render-only
  polygonOffset by install order cures coplanar contact z-fighting.
- Headless drive extended: D3 full pass + D4 switch/rebuild/orbit/
  evidence, 0 console errors, screenshots read back. Suite 29 green.
- Remaining limits: products all null (no system docs — honest); D1/D2
  crops have no specs yet (selector shows "no specification" honestly);
  interference still UNVERIFIED (AABB-level v0); Spark/SAM
  interpretation, CadQuery/Blender, Modal still future per PLAN.

## 2026-10-29 — frontend migrated to React + TypeScript

- Rebuilt the viewer as `web/` (Vite + React + strict TS, three 0.149
  npm, no UI framework): same layout/CSS, same element IDs, verbatim
  Three.js viewport port. FastAPI serves `web/dist` at /.
- Unmodified drive script passes against the new UI (both variants,
  0 console errors); screenshots match. Vitest covers badge logic
  (4 tests). Legacy `app/static/` deleted; drive moved to `e2e/drive.py`.
- Suite: 29 pytest + tsc + 4 vitest green. See `docs/FRONTEND_MIGRATION.md`.

## Milestone ledger

| Milestone | State | Evidence |
|---|---|---|
| 1 audit + plan | DONE | docs/GAP_ANALYSIS.md, PLAN.md, STATUS.md |
| 2 vertical slice | DONE | app/, tests/ (22 green), E2E PASS 0 FAIL |
| 3 traceable interpretation | DONE (v0) | overlay, provenance badges, corrections API |
| 4 accurate components | DONE (v0 mesh) | ribbons/fillet/bead/wedge; CadQuery next |
| 5 viewer + sequence | DONE (v0) | explode/clip/playback/param-edit in viewer |
| 6 eval + submission | PARTIAL | EVAL/ARCH/DEMO done; video + freeze Wed 4 Nov |

## Known limitations (v0 slice)

- Server-side page rasterization: none — crops come from embedded figures
  (true for this reference PDF); vector-only pages return an honest
  "no embedded figures" result, not a fake render. Client renders pages via
  PDF.js (viewer roadmap).
- Extrusion depth ("illustrative extent", default 600 mm) is assumption-only
  and excluded from accuracy checks — flagged in spec + viewer.
- Slab thicknesses, frame profile dims, membrane physical thickness: no
  numbers in D3 → recorded assumptions, UNVERIFIED, never counted as passes.
- Jobs run in-process (states queued→running→done/failed are real, same
  process). Modal adapter is an interface + honest "not configured" until
  credentials exist.
