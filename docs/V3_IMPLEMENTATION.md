# V3 implementation and validation

Implementation of `PLAN_V3.md`, 29 October 2026. The original plan is unchanged.
This file records the implementation and its evidence; it does not replace the
acceptance criteria or certify construction compliance.

## Implemented

- SQLAlchemy/Alembic repository for immutable versions, source artifacts,
  components/builds, jobs, model/tool checkpoints, verification, renders,
  critics, reviews and memories. SQLite is the local default; PostgreSQL uses
  row locks and `SKIP LOCKED` for independent workers.
- Durable leases, heartbeat, fencing, cancellation, retry/backoff, idempotency,
  atomic tool effects and persistent resource/token budgets. Restarting a worker
  replays saved results and keeps outstanding reservations charged.
- Native Muse tools for reading, recall, writing/patching, CAD/reuse, measuring,
  rendering, independent critique, SAM, proposing lessons and guarded finish.
  Program authoring uses structured output inside `write_program` because live
  native calls repeatedly escaped/malformed large nested program arguments.
  This follows [Meta's structured-output interface](https://dev.meta.ai/docs/structured-output).
- CAD repair receives the previous code and exact current bindings. Workplane
  origin/relative-center guidance follows the [CadQuery class reference](https://cadquery.readthedocs.io/en/latest/classreference.html#cadquery.Workplane.workplane).
  The writer also receives the required local CadQuery bounds, derived by
  undoing rigid placement, to avoid depth/height and offset ambiguity.
- Small program edits support stable references such as
  `/parts/@waterstop/placement/z` and `/params/@height/value`. Source metadata
  and version identity are supplied by the server. Gates run on every write.
- Layers use exact primitives. Objects use AI-written `build(p)` CadQuery in
  private, network-blocked sandboxes. A separate trusted process imports STEP,
  measures the B-Rep and produces the viewer mesh. Geometry is never scaled to
  fit an envelope. Failed CAD is a labelled placeholder with a required FAIL.
- Source-dimension ownership, provenance/quote retention, exact bounds,
  supported feature measurements, planar coverage/separation and installation
  dependencies are checked. Human-approved requirements remain protected;
  unreviewed AI interface proposals can be repaired.
- Named-size checks include CAD constructor sizes, workplane/center offsets and
  pattern spacing. Scaling and affine geometry fitting are rejected.
- Solid envelope blocks cannot stand in for hollow fixtures. The critic also
  checks recognisable WC/basin/vanity form; correct bounds alone are insufficient.
- The headless viewer renders assembled physical iso/front/top views, with and
  without tags. It checks scene identity and rejects blank images. Verification,
  render and critic IDs are bound to an exact version and manifest.
- A reserved budget tail pays for rendering and independent critique before
  stopping a draft. `finish(done)` checks the evidence again, including artifact
  integrity. Publication additionally requires human acceptance of that version.
- The UI has persistent job progress, trace/cancellation, version history,
  historical scenes and review/recheck flows. Native generation is the default.
- Legacy import preserves corrected specs and physical/display scene/GLB
  snapshots. The six fixture generators are absent from the active registry and
  v3 grammar. Historical scenes load from archived bytes.
- Active lessons are supplied with drawing reads and structured authoring, as
  well as through `recall`. Proposed lessons require acceptance. A lesson cannot
  supply another drawing's provenance; changed human values remain `user_input`.
- Repeated verifier failures and human-accepted repairs of critic issues create
  deduplicated proposed memories linked to their evidence. They remain inert
  until a reviewer activates them.
- Evaluation records every terminal outcome, tokens/cache usage, source
  measurements, labels, CAD/render attempts, critic verdicts and timings.
  Revisions retain previous attempts and disclose reused successful results.

## Running locally

Install the pinned requirements and build `web/`. Linux rendering also needs
`python -m playwright install --with-deps chromium`; local macOS can use installed
Chrome. Set the configured Meta key in the existing `.env` or environment.

Before switching an existing library, inspect and back it up:

```sh
.venv/bin/python -m app.versioning --dry-run
.venv/bin/python -m app.versioning --backup var/backups/pre-v3
```

The backup directory must be new. Import is idempotent and never deletes the
original library. The local library was backed up under
`var/backups/pre-v3-20261029` and imported. The server now also imports any
records not yet in the database at startup. Seeds are not imported; they are
served from code, and an earlier import of a seed is ignored.

Run the API and worker in separate terminals with the same environment:

```sh
export SETOUT_VAR=var
export SETOUT_WORKER=external
export SETOUT_RENDER_URL=http://127.0.0.1:8000
.venv/bin/python -m uvicorn app.main:app --port 8000
```

```sh
export SETOUT_VAR=var
export SETOUT_WORKER=external
.venv/bin/python -m app.worker
```

For PostgreSQL, set `SETOUT_DATABASE_URL` in both processes. For multiple hosts,
use the same mounted artifact directory via `SETOUT_ARTIFACT_ROOT`; the Modal
volume adapter also uses `SETOUT_ARTIFACT_VOLUME` for publish/reload visibility.
The public demo remains read-only. No deployment was performed by this work.

Limits per detail run are 32 model calls, 48 tool calls, 320,000 tokens including
cached input, eight CAD calls, six renders and 900 seconds. The final 100,000
tokens are reserved for review once a draft exists. Generation uses low agent
reasoning effort; revision uses medium. Nested program/CAD/critic calls have
their own checkpoints and consume the same run budget.

## Validation evidence

- Default suite: **156 passed, one browser integration test skipped**.
- Independent PostgreSQL crash/replay/lease/concurrent-budget suite: **20 passed**.
- Frontend: **13 tests passed**, production TypeScript/Vite build passed.
- Real viewer rendering and persistent trace visibility after browser reload
  passed locally and in a private Linux Modal sandbox.
- Live native tools, image continuation and replay without resending reasoning
  passed. The complete provider responses remain in the durable trace.
- Live AI-written WC, vanity and basin passed independent extent measurement.
  A failed vanity depth of 775mm was detected against 450mm, repaired by the
  model and remeasured. The original failed component remains in history.
- Live memory on/off probe passed: a synthetic reviewer convention changed an
  unprinted slab assumption from 100mm to 123mm. Both arms read the held-out
  drawing's 75mm wall height, rather than copying the training drawing's 50mm.
  The transferred convention stayed an assumption. This is a functional
  correction-transfer check, not a field-accuracy claim.

Local evidence:

- `var/v3-objects-live/result.json`: measured objects and repair history.
- `var/v3-memory-live-checked/result.json`: live memory A/B and provenance.
- `var/eval-v3-final-demo/eval/`: both own drawings, attempts, traces and results.
- `var/eval-v3-release-batch/eval/`: 24-crop manifest, attempts and v2 comparison.

The 24-drawing comparison is complete: four jobs succeeded and twenty require
review; sixteen produced buildable geometry. Full counts and the frozen code
identity are in [V3 evaluation evidence](V3_EVAL.md). The original batch preceded
the final verifier/controller fixes and is not an accuracy claim for final code.

Both own drawings now have passing assembled renders and independent critics,
with **188 required PASS, zero required FAIL/UNVERIFIED, and all four printed
dimensions measured**. The section retains eight of twelve READ labels; the
elevation retains twelve of twelve. This is not complete human fidelity review.
The final evidence refresh passed under `setout-v3-c91e0312a946d117`.
`var/eval-v3-final-demo/eval/acceptance.json` binds the current verification,
render and critic evidence to each exact version; both completion guards return
an empty error list. The adjacent `review.html` contains the own-drawing images.
These are results after guided revisions and AI-component reuse, not a claim of
one-shot autonomous success.

Visual inspection rejected an earlier box-shaped fixture pass, primitive
fittings misclassified as finishes and missing cabinet supports. The gates were
strengthened and the agent repaired those drafts. The elevation now contains
AI-written CAD for WC, cabinet, basin, tap, glass screen, shower rose/mixer,
floor waste and cabinet legs. All original attempts remain in the private trace.

| Plan milestone | Evidence |
|---|---|
| M1 Data/jobs | Abrupt process death before/after tool commit, replay/fencing/concurrent claims on SQLite and PostgreSQL; local two-detail library backed up and imported |
| M2 Agent sees work | Both own drawings complete, matching physical renders/critics, four measured source dimensions; browser test confirms saved trace visibility after reload |
| M3 AI-made objects | WC/cabinet/basin live measured builds and inspected renders; six legacy fixtures absent from active registry; old scenes load from archived bytes |
| M4 Memory | Live held-out on/off convention-transfer probe with a synthetic reviewer, plus provenance and activation tests |
| M5 Evaluation | All 24 crops accounted for and compared with recorded v2; four complete, twenty require review; warm starts, prior attempts and snapshot limitations disclosed |

## Review fixes (29 Oct 2026, after the implementation review)

A review against `PLAN_V3.md` found gaps; these were fixed on branch
`feat/v3-agent`. The numbers above predate them.

- **Trade-agnostic.** No vocabulary of ours decides a part's role or form. The
  READ classifies each label as layer, object or other, and a part carrying it
  takes the matching role. Objects declare `form` (shaped/block), which the form
  check reads. Prompts use cross-trade wording.
- **One store for AI details.** Seeds stay in code; every AI detail is a database
  version. The v2 pipeline (generate/critique/re-model/locate threads,
  `app/review.py`, v2 CAD fitting and authoring grammar) is removed. An old
  file library imports at startup. Seeds are no longer imported: an earlier
  import of a seed is ignored.
- **Labels.** The authoring schema offers only the READ's labels (or null). The
  prompts ask for every layer/object label to be covered, with no part cap, and
  for rejected proposals to be patched. A non-blocking `label-coverage` check
  lists what is missing. Cause analysis of the 24-crop batch: 31 labels reworded
  by the model, 27 never modelled, 9 lost to rejected programs, 50 that were
  never parts (zone names, notes, tables).
- **Memory.** A correction proposes lessons written from the version diff. The
  detail page lists lessons and lets a named reviewer switch them on.
- **Jobs.** The first migration is frozen, with a schema-drift test. A worker is
  killed mid-run (SIGKILL) and another resumes it (`tests/test_resume_e2e_v3.py`).
  The embedded worker starts with the server. The agent loop stops if it makes
  no progress.
- **Trace and renderer.** Compact, paged trace with the renders the agent saw;
  browsers are reused across renders.
- **Found in Chrome:** a structured call that narrated before its JSON (now the
  last valid message is read); `min_coverage: 90` read as a fraction (now
  rejected when authoring); the parts column collapsing on narrow windows.

Evidence: 160 backend tests pass (10 Postgres/browser variants skipped
locally); 13 frontend tests; `e2e/drive.py` DRIVE OK.

Live, on our own section drawing:
- **Generation** kept all 8 layer/object labels verbatim. It survived a server
  kill and resumed from its saved steps.
- **"Recheck with AI"** took 210 s and 228k tokens. Checks went from 41 PASS /
  10 FAIL / 9 UNVERIFIED to 66 / 1 / 6; the agent fixed the coverage unit after
  the new error message and modelled both objects in the CAD sandbox. The
  critic passed.
- **Result:** the detail correctly remains `needs_review`. The two printed
  dimensions stay UNVERIFIED (the agent's face selectors are not supported) and
  one `terminates_against` relation fails.

## Boundaries

Unsupported or ambiguous feature measurements stay UNVERIFIED. Required
UNVERIFIED results block completion. Wall-thickness and general angle selectors
are not implemented; bore/radius measurements require unambiguous cylindrical
surfaces. Planar primitive/CAD interfaces are supported; arbitrary curved
interfaces remain UNVERIFIED.

The 24-drawing comparison uses the previously recorded v2 results. Their commit
and human fidelity annotations were not recorded. V2's build/trace counts used
weaker checks, so they are not equivalent to v3 completion or measured-source
PASS. The comparison must disclose this and any warm starts. Human fidelity
remains separate from code checks and the model critic. Third-party source
documents and runtime databases remain outside version control.
