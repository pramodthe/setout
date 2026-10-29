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
- Meta API: access previously verified — `ncc/benchmark/results.json`
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

## Restructure — legacy track grouped under `ncc/`

- `rules/`, `generator/`, `extractor/`, `viewer/`, `perception/`,
  `benchmark/`, `spec/SPEC.md`, `spec/VIDEO.md` moved to `ncc/` (pure
  `git mv` + path-constant updates; generator output byte-identical).
- `scripts/regen.sh` + `run_viewer.sh` added alongside the `.bat` files.
- Nothing replaced was left behind: `app/static/` (vanilla viewer) was
  already deleted at the React cutover; `ncc/viewer/` is the live NCC
  track, kept. README/CLAUDE.md/AGENTS.md updated to the new layout.

## 2026-10-29 (later) — Playground v2 direction + viewer foundation

- New direction: a generative installation playground — browse details by
  situation or product, with real junction geometry and AI throughout. Plan is
  in `docs/PLAYGROUND_PLAN.md`.
- Viewer (`web/src/viewport.ts`) rebuilt:
  - OrbitControls, studio image-based lighting (RoomEnvironment), ACES tone
    mapping, soft shadows on a shadow-catcher ground.
  - Procedural PBR materials drawn by our own code (`web/src/materials.ts`,
    20 classes) on true-scale box-projected UVs (`web/src/uv.ts`).
  - Stepped layer cutaway applied to the real section.
  - Labels use the drawing's own names, with stacked leader lines and click to
    select.
- Camera API for the coming AI guide: `flyTo`, `setView`, `isolate` (ghosts
  the rest), `playIntro` (bottom-up build in install order).
  - Workbench toolbar: fit / iso / section / top / solo / cutaway / labels / build.
  - Selecting from the list or the drawing now flies to the part.
- Suite: 29 pytest, tsc, 12 vitest, e2e drive green (0 console errors).

## 2026-10-29 (evening) — AI generation: any drawing → 3D

- User test found the app could not generate 3D from any uploaded document.
  The server log showed no build request was ever sent; only the two
  hand-typed seeds worked.
- Spec v2 detail grammar (`app/program.py`):
  - named params with provenance, plus parts from a closed shape set (box,
    sheet, post, rod, wedge, fillet) whose dims are expressions of params;
  - kinds: room / section / assembly;
  - a whitelisted evaluator;
  - provenance flows through expressions (datums are neutral);
  - compiles to DetailSpec v1, so generators, verification and exports are
    reused. Generators accept per-part z0/z1.
- AI pipeline (`app/generate.py`, `app/meta_client.py`):
  - Muse READ pass (labels, dims, type, rotation), then a COMPOSE pass (program
    JSON, strict schema), then code gates;
  - gates: a drawing value needs its quote in the READ and the number in the
    quote, otherwise it becomes an assumption; labels must be verbatim;
  - compile + build, with one verifier-feedback retry;
  - stored under `var/library/`.
- API and UI:
  - `POST /api/generate` and `GET /api/generate/{id}`; specs are listed and
    matched by document sha + crop.
  - A "Generate 3D with AI" button on any drawing without a detail, with live
    progress, then auto-build and the intro animation.
- Viewer: room kind uses a front-right camera and a stepped floor
  staircase (finishes step back to reveal the membrane).
- Live results on the wet-area guide (muse-spark-1.3):
  - p6_Image53 room: 21 parts, 7/7 labels placed, 6 drawing values, 112 s,
    ~35k tokens, 53 PASS 0 FAIL.
  - p15_Image110 section: 11 parts, 69 s, 28 PASS 0 FAIL.
  - Weak spots: dimension-to-part assignment is sometimes loose, and section
    proportions read flat. Next: a review screen and prompt work.
- Suite: 44 pytest (program/gates included), 12 vitest, e2e drive green.
- Tested in the user's own Chrome (Claude in Chrome):
  - p10_Image83 "Bath lip waterstop over tiled shelf" generated live (10 parts,
    27 PASS 0 FAIL).
  - Fixed: JPEG 2000 figures (5 of 24 in the wet-area guide) were stored raw
    with a .png name, so browsers and the model API could not read them. They
    are now re-encoded as PNG at upload (`app/store.py`).
  - Fixed: selecting a drawing with no detail left the previous detail's 3D on
    screen; it now clears.

## 2026-10-29 (night) — richer grammar: profile, revolve, sweep, fixtures

- New shapes the model can describe, all still verified:
  - `profile`: any closed outline extruded, with ear-clipping caps, so concave
    angles and channels work;
  - `revolve`: a lathe around a vertical axis;
  - `sweep`: a tube along a 3D path with parallel-transport frames;
  - `fixture`: bath, shower tray, WC, basin, vanity and door, generated
    parametrically inside an envelope the program sizes.
- Verification:
  - profile and revolve: built extents equal the program;
  - fixtures: must stay inside their envelope.
- Viewer: ceramic and glass material classes (glass is see-through, respected
  by ghosting and labels); all generated meshes render double-sided.
- Live in the user's Chrome, p7_Image67 "Shower base installation":
  - 22 parts, chosen by the model: fixture:bath, fixture:shower_tray,
    revolve:floor waste, posts and boxes;
  - 11 printed values confirmed by the gates (1800 / 1500 / 25 / 40 / 150 mm),
    0 gate notes, 61 PASS 0 FAIL, 152 s.
- Suite: 55 pytest, 13 vitest, e2e drive green.
- Next: CadQuery code path in a Modal sandbox (for shapes the grammar can't
  express), then the render → compare → correct loop.

## 2026-10-29 (late) — CadQuery path (Modal sandbox) built; awaiting Modal login

- New `cad` shape: an envelope plus a brief, max 3 per program, illustrative
  only. Each cad part gets its own Muse pass that writes CadQuery
  `build(W, H, D)`.
- `app/cad_sandbox.py`:
  - static AST gate (imports only cadquery/math; no exec/eval/open/dunders;
    `build` required);
  - runs in a Modal Sandbox (`block_network=True`, 1 CPU, 1 GiB, 120 s);
  - STL back on stdout, mapped to Setout axes by a rotation (no mirror) and
    fitted into the envelope (uniform shrink only, recorded).
  - Model code never runs in this process.
- Pipeline:
  - CAD parts run in parallel, with one retry using the sandbox error;
  - a failed or unconfigured sandbox gives a box plus a gate note, so a detail
    never fails because of CAD;
  - the mesh is stored at `var/library/cad/<detail>/<part>.json`, and the code
    and sha go in the record.
- Verification: the AI-modelled part must stay inside its envelope (PASS/FAIL),
  plus a C-cad N/A marker ("illustrative, never a verified dimension").
- `modal==1.6.3` added to requirements. The local Modal token fails
  authentication; the user needs to run `modal token new`. The live sandbox
  smoke test is pending.
- Suite: 71 pytest (16 new CAD tests, using a stand-in runner), 13 vitest, e2e
  drive green.
- Modal re-authenticated by the user. Live: CadQuery 2.8.0 (pinned) runs in a
  no-network sandbox; cold start ~45 s, then 20–40 s per part.
- When composing, Muse did not choose `cad` for p14_Image104: the verifiable
  grammar sufficed (4 profiles, sheets, rods, wedge, fillet).
- Added "Model with AI (CadQuery)" on any part of an AI-generated detail
  (`POST /api/specs/{id}/cad`, `generate.remodel_part`). It re-models that part
  into the space it occupies and keeps its id, label and install order.
- Live in the user's Chrome: grate_top (a plain box) became a perforated
  stainless grate — staggered holes cut through, 8148 triangles, scale 1.0,
  45 PASS 0 FAIL. Code stored with its sha.
- Suite: 72 pytest, 13 vitest, e2e drive green.

## 2026-10-29 (late) — render → compare → correct (verifier-guarded)

- The viewer `snapshot(view)` renders iso and section views and stamps each
  visible part with its id.
- `POST /api/specs/{id}/critique` (`generate.critique`): Muse gets the original
  drawing, the renders, the current program and the READ. It returns issues
  (part, problem, fix) and a corrected full program.
- The correction goes through the same gates, compile and build. The guard
  (`quality`) rejects it if:
  - failures increase, or
  - drawing values driving geometry drop, or
  - verbatim labels drop.
  CAD parts are frozen. Each round is stored in `revisions`.
- UI: a "Check against drawing (AI)" button, and one automatic round right
  after each AI generation.
- Live in the user's Chrome on p15_Image110: 4 issues found:
  - an invented 30 mm door-track block (removed);
  - the "Floor substrate" label on the wrong slab (moved);
  - floor tile overlapping the riser tile (shortened);
  - membrane upstand stopping short (extended).
  Accepted, 26 PASS 0 FAIL, ~2 min.
- Suite: 77 pytest (5 new guard tests with a stubbed model), 13 vitest, e2e
  drive green.

## 2026-10-29 (late) — library front page + AI installation guide

- Routes: `#/` library, `#/d/<id>[?part=<id>]` detail page, `#/workbench`
  add-from-PDF (the old screen; `e2e/drive.py` now opens it there).
- Library (`app/library.py`, `GET /api/library`, `web/src/Library.tsx`):
  - browse by situation (sector › area › element, from each detail's
    situation; seeds now carry one);
  - browse by component/product (every detail containing a part type; product
    refs are still null, honestly stated);
  - search, cards with the source drawing as thumbnail
    (`GET /api/specs/{id}/thumb`), and badges for human vs AI.
- Detail page (`web/src/DetailView.tsx`):
  - `GET /api/specs/{id}/scene` builds and verifies on demand;
  - parts in install order with provenance badges and evidence;
  - view, solo, explode, cut and install-step controls; build-up intro;
  - deep link to a part (fly + isolate).
- AI guide (`app/guide.py`, `POST /api/guide`, `web/src/Guide.tsx`):
  - Muse answers with a tour of narrated steps plus viewer actions (focus,
    isolate, show_all, explode, section, view, install_step, select);
  - grounding check: every spoken number must exist in the detail (params,
    drawing values, quoted labels, step numbers); one retry naming the
    offenders, and anything left is flagged on the step;
  - unknown part ids are dropped and values clamped.
- Live in the user's Chrome: "walk me through the install" on the AI bathroom
  gave a 6-step tour driving install step 13/21, isolating the waterproofing,
  citing 150/50/10 mm "from the drawing".
- Fixed a crash: an effect returned `scrollIntoView(...)`, which is a Promise
  in current Chrome (smooth scroll), so React called it as cleanup.
- The library now has 10 details, including 3 the user generated from their
  own PDFs.
- Suite: 82 pytest, 13 vitest, e2e drive green.

## 2026-10-29 (night) — review, SAM links, batch eval, hosted demo

- Review and publish (`app/review.py`, `web/src/ReviewPanel.tsx`):
  - printed values listed first, with quote and driven parts (click to isolate);
  - accept or fix (edits recompile, so dependent dimensions move); labels only
    from printed text;
  - publish as reviewed by a named person;
  - a reviewer can never promote a value to "drawing".
- SAM 3.1 drawing links (`app/sam.py`):
  - the drawing is turned upright using the READ rotation (SAM finds nothing
    sideways);
  - up to three names per part (label, type, everyday synonym); best box ≥ 0.5;
    thin layers are skipped;
  - bathroom: 5 parts located (bathtub 0.92, sink 0.84, floor drain 0.80, tap
    0.70, waterproofed walls 0.50);
  - drawing panel on the detail page: click a box to fly to the part.
- Batch evaluation (`eval/batch_eval.py`, `docs/EVAL.md`), 24 drawings:
  - 24/24 built, 0 FAIL;
  - 86 printed values confirmed (55 drive geometry);
  - 173/221 labels used verbatim;
  - 0 gate demotions; median 111 s, ~30k tokens.
- Hosted demo (`deploy/modal_app.py`) at
  https://pthebe-sfsu--setout-demo-web.us-east.modal.run:
  - public mode is read-only, never serves source drawings, and rate-limits
    the guide;
  - currently shows only the two built-in seeds;
  - AI guide switched on (`SETOUT_DEMO_GUIDE=1` + secret `setout-meta`).
    "Walk me through the install" on D3 played all 6 steps live, with no
    ungrounded numbers. Fix: the guide flag is baked into the image env, because
    a secret attached only at deploy time made the containers crash-loop
    ("Function has 2 dependencies but container got 3 object ids").
- `docs/DEMO.md` rewritten for the new flow, with the rule: no third-party
  drawings on screen.

## 2026-10-29 (night) — own demo drawing

- `scripts/demo_drawing.py` draws a shower step-down section, our own work, so
  the video and public demo never need a third-party drawing. Its only printed
  numbers come from `ncc/rules/rules.json` (25 MIN. step-down, 1800 MIN.
  membrane above finished tile).
- Pipeline result: 94 s, ~30.6k tokens; 13 labels read, 8 placed; both printed
  values kept as drawing minimums, everything else an assumption; 0 gate
  notes; 33 PASS, 0 FAIL. The guide's walkthrough quotes both values.
- SAM 3.1 finds nothing on it (empty for every prompt, while the bathtub
  control still returns 0.92): flat sections have no objects for SAM to find.

## 2026-10-29 (night) — library cards show the 3D, not the drawing

- Cards used the source drawing as their thumbnail, so the local library
  showed third-party pages (unrecordable) and the public one showed blank
  text boxes. Now each detail page posts one clean render after the build-up
  (`POST /api/specs/{id}/preview`, JPEG only, known ids only); cards show it,
  and public mode can read but not write it.
- Generated for all 11 local details by opening each page once.

## 2026-10-29 (night) — PR #10 merged; public demo updated; AR cut

- PR #10 squash-merged (`40c1d96`). PR #11 has the demo drawing and card renders.
- Public demo now shows three details: the D3 and D4 seeds and the step-down
  made from our own drawing, each with a card render. Details derived from
  third-party documents are not published there.
- Decision: camera/AR (Jarvis) is left out of the hackathon submission. It
  needs real membrane-stage footage, which we don't have. It stays future work
  (README §12).

## 2026-10-29 (night) — own bathroom elevation; SAM never shares a box

- `scripts/demo_drawing.py` now also draws a bathroom elevation: a WC, a
  vanity with basin, a mirror and a shower with rose, mixer, screen and waste.
  The printed numbers (1800 MIN. membrane, 150 MIN. water resistant above the
  basin) come from `rules.json`. Both drawings go into one 2-page
  `setout-demo.pdf`.
- Pipeline on the elevation: 115 s, ~40.6k tokens; 16 parts (the shower rose
  modelled in CadQuery on Modal); both values kept as drawing minimums; 0 gate
  notes; 39 PASS, 0 FAIL.
- Bug found and fixed in `sam.locate`: the basin tap and the shower mixer
  both asked for "tap" and got the same box, so the shower mixer pointed at the
  basin tap. A box claimed by two parts now goes to neither, unless exactly one
  found it by its own drawing label. There's a regression test, and it fails on
  the old code. Result: 3 correct links (WC 0.88, vanity 0.61, shower rose
  0.62). Clicking the WC box on the drawing flies the camera to the WC.

## 2026-10-29 (late) — video dress rehearsal on our own PDF

Every shot in `docs/DEMO.md` was run in Chrome. Fixed along the way:
- **Library cards showed "0 drawing values" on AI details.** They now count the
  printed values confirmed against the drawing.
- **The workbench self-check could render the wrong detail.** Switching detail
  kept the old 3D, so the self-check compared detail A's render with detail B's
  drawing and reported bogus issues. Switching now clears the 3D, and the check
  runs only on the 3D of the detail being checked.
- **Build-up order.** Compose and self-check produced "tiles before membrane",
  and one accepted self-check moved a membrane in front of the tiles. Both
  prompts now carry a general build-up rule: a covered layer lies behind what
  covers it and is installed first; never bring one forward to make it
  visible. The regenerated elevation is correct: membranes, then tiles, then
  fixtures.
- **Review:** the whole value description is clickable (it isolates the
  parts it drives).
- **Viewer:** parts between the camera and the focused part fade (raycast),
  so a room wall no longer hides the part the camera flew to. Snapshots ignore
  the fade.

Also:
- The AI library moved from the session scratchpad to `var/demo` (gitignored),
  and the batch evaluation to `var/eval-run`.
- e2e: DRIVE OK, 0 console errors.

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
