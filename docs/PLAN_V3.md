# Setout v3: agent architecture

Proposed 29 Oct 2026. It replaces the fixed AI pipeline in `app/generate.py`.
The decisions marked **[decide]** need the project owner's sign-off before work
starts on them.

## Why: the gaps in v2

1. **No agent.** Generation is a fixed chain: read → compose → code gates →
   optional self-check. Muse never chooses a next step, has no tools, and keeps no
   memory between runs.
2. **Jobs aren't persistent.** They live in a Python dict (`GEN_JOBS` in
   `app/main.py`), so a restart loses them.
3. **No real database.** SQLite holds only `documents` and `builds`. Details,
   versions, reviews and SAM links are JSON files, and nothing records what an AI
   run did.
4. **The 3D is mostly hand-written.** The model picks parts and dimensions, but
   every shape comes from 18 generators in `app/geometry.py`. Six of them are
   fixed models (bath, WC, basin, vanity, shower tray, door). AI-written
   CadQuery exists, but only for up to 3 parts.
5. **The model never sees what it built.** The only visual check is optional
   and needs the user's browser to take the pictures. It once accepted a
   membrane moved in front of the tiles.

## Verified facts this design relies on

- **Muse Spark native tool calling works** on our key (checked 29 Oct). The
  `/responses` call takes `tools: [{type: "function", name, description,
  parameters}]`. The output carries `{type: "function_call", call_id, name,
  arguments}`. We send back `{type: "function_call_output", call_id, output}`
  and the model continues. Non-streaming works, and the output also contains
  `reasoning` items.
- **Prompt caching happens server-side.** The second turn reported 497 of 642
  input tokens as cached. Long agent conversations are cheaper than their
  length suggests.
- **CadQuery 2.8 runs in the no-network Modal sandbox** (`app/cad_sandbox.py`).
- **Playwright and Chromium are installed** (`e2e/drive.py`), so headless
  rendering of our own viewer is available on the server.

## Target architecture

```
                 ┌──────────── FastAPI (thin) ────────────┐
 browser ──────▶ │ details · versions · jobs · runs · SSE │
                 └───────┬───────────────────────┬────────┘
                         │ enqueue / read        │ read
                         ▼                       ▼
                 ┌──────────────── Database ───────────────┐
                 │ documents figures details versions params│
                 │ parts components jobs job_events         │
                 │ agent_runs agent_steps renders memories  │
                 │ reviews sam_regions                      │
                 └───────▲─────────────────────────────────┘
                         │ lease · checkpoint · results
                 ┌───────┴──────── Job workers ────────────┐
                 │ generate_detail → Agent harness (Muse)   │
                 │   tools: read · recall · program · cad · │
                 │   build+verify · render · critic · sam · │
                 │   remember · finish                      │
                 └──┬──────────┬───────────┬──────────┬────┘
                    ▼          ▼           ▼          ▼
               Meta API   Modal sandbox  Renderer   SAM 3.1
               (Muse)     (CadQuery)     (headless  (Meta API)
                                          viewer)
```

## The core rule, restated [decide]

Today's invariant says the model never writes geometry. Proposed replacement:

> **3D geometry is generated from named values, never placed by hand.**
> - **Layers and substrates** (slabs, screeds, membranes, beds, tiles, walls)
>   are exact primitives from the program grammar (box, sheet, profile,
>   revolve, sweep).
> - **Objects** (WCs, vanities, waterstops, grates, brackets, anchors…) are
>   parametric CadQuery written by the model and run in the no-network sandbox.
>   Every dimension the code uses is a named value with provenance. The
>   verifier measures the solid, and a mismatch is a FAIL.
> - No hand-modelled object ships in the generator registry. If the sandbox
>   fails, the object becomes a box labelled "placeholder (CAD failed)" that can
>   never PASS.
> - The honesty gates are unchanged: a drawing value needs its quote, and
>   labels are verbatim.

## Data model

**[decide]:** use Postgres in deployment and SQLite for tests and
single-machine development, through SQLAlchemy 2 with Alembic migrations.

- **Postgres:** multiple workers lease jobs safely (`FOR UPDATE SKIP LOCKED`),
  and six developers share one schema.
- **SQLite:** keeps `pytest` free of any server.

| Table | Key columns |
|---|---|
| `documents` | id, sha256, filename, pages, created_at |
| `figures` | id, document_id, page, crop_key, width, height, rotation_deg, png_path |
| `details` | id, slug, title, situation (json), kind, current_version_id, review_status |
| `detail_versions` | id, detail_id, n, program (json), spec (json), origin, created_by (run or user), quality (json), created_at |
| `params` | id, version_id, name, value, unit, provenance, kind, source_quote, note |
| `parts` | id, version_id, part_key, type, role, label, shape, component_id, install_after (json) |
| `components` | id, name, type, code, code_sha, param_schema (json), brief, measured (json), status, uses, created_by_run |
| `jobs` | id, kind, state, payload, result, attempts, max_attempts, lease_owner, lease_until, heartbeat_at, error, timestamps |
| `job_events` | id, job_id, at, kind, message, data |
| `agent_runs` | id, job_id, detail_id, model, budget, tokens_in, tokens_out, cached_tokens, steps, outcome |
| `agent_steps` | id, run_id, n, kind (model / tool), tool_name, call_id, arguments, output, image_ids, tokens, ms |
| `renders` | id, version_id, view, png_path, tags, created_at |
| `memories` | id, scope (global / manufacturer / type / detail), kind (lesson / convention / correction / failure), text, tags, source, weight, uses, last_used_at |
| `reviews` | id, version_id, reviewer, action, data, at |
| `sam_regions` | id, version_id, part_key, prompt, confidence, box |

Every version is immutable. An edit, a correction or a review fix creates a new
version, so history and diffs come for free. The existing JSON library and seeds
are migrated by a script.

## Persistent jobs

- **States:** `queued → running → succeeded | failed | needs_review |
  cancelled`.
- **Leasing:** a worker leases a job for 60 s and heartbeats every 15 s. An
  expired lease returns the job to the queue, with exponential backoff, up to
  `max_attempts`.
- **Checkpoints:** every agent step is written to `agent_steps` before the next
  one starts. On resume, the harness rebuilds the conversation from the stored
  steps (messages, `function_call`, `function_call_output`) and continues.
  Tools are idempotent: building a version that already exists returns it.
- **Job kinds:** `generate_detail`, `revise_detail`, `cad_component`, `render`,
  `locate`, `batch_eval`. The guide stays a short request.
- **Concurrency limits** are per kind (Meta API rate, Modal sandboxes,
  renderer pages).
- **API:** `POST /api/jobs`, `GET /api/jobs/{id}`,
  `GET /api/jobs/{id}/events` (SSE), `GET /api/runs/{id}/steps`.

## Agent harness

A loop around Muse Spark using its native tools: the model calls tools, the
harness runs them and returns the results, until the model calls `finish` or a
budget runs out.

| Tool | Input | Output |
|---|---|---|
| `read_drawing` | figure_id | labels, printed values with exact quotes, drawing type, rotation (today's READ pass) |
| `recall` | query, part types | relevant memories and reusable components |
| `write_program` / `patch_program` | program, or edit ops | compile result, gate demotions, new draft version id |
| `model_component` | part_key, brief, named params | component id, measured size, sandbox log |
| `reuse_component` | component_id, params | as above, without writing new code |
| `build_and_verify` | version_id | PASS / FAIL / UNVERIFIED per check, with messages |
| `render` | version_id, views | images with part tags (sent back as `input_image`) |
| `critic` | version_id | an independent visual judgment: drawing vs renders → issues (a separate Muse call, not the agent grading itself) |
| `locate_on_drawing` | version_id | SAM 3.1 regions |
| `remember` | text, tags, scope | memory id |
| `finish` | status, summary, open issues | ends the run |

**Rules that live in code, not in the prompt:**
- **Gates.** The honesty gates run inside `write_program`, so the model can't
  skip them.
- **Finishing.** `finish(done)` is refused unless the latest version has 0
  FAIL and the latest critic verdict passes. Otherwise the run must finish as
  `needs_review` with reasons.
- **Budgets.** Each run has limits on steps, tokens, CAD runs and wall-clock
  time. Hitting one ends the run as `needs_review`.
- **Tracing.** Every step is stored and shown live in the UI.

**Spike first:** `function_call_output` is text, so images go back as a
follow-up user message with `input_image` parts. Confirm the model reads them
in the same turn.

## Memory

- **Run memory:** the full step trace in `agent_steps`. It also powers resume.
- **Long-term memory** (`memories`). Written when:
  - a critic issue is fixed and accepted (lesson);
  - a reviewer edits a value or label (correction);
  - the same verifier failure repeats (failure pattern);
  - people add conventions by hand.

  Retrieved at the start of a run (by drawing type, part types and
  manufacturer) and through `recall`. **A memory never supplies a drawing
  value**, and the gates still require the quote from this drawing.
- **Component memory** (`components`): CadQuery the agent has written, reused
  with new values. Promoted to `reviewed` after a human accepts it.

## 3D generation

- **Layers:** today's grammar primitives, exact and verified.
- **Objects:** each component is a function `build(p) -> cq.Workplane`, where
  `p` holds only named params. The sandbox returns STL plus measured bounds
  and features. The verifier compares them with the param-driven envelope.
- **Removed:** the six hand-written fixtures leave `REGISTRY`, the grammar and
  the prompts.
- **Roles:** parts declare a `role` (substrate / covered_layer / finish /
  object). This enables a trade-agnostic **build-up check**: when two parts
  touch, a covered layer must be installed before the part that covers it.
  That would have caught "tiles before membrane" (29 Oct).

## Renderer (the agent's eyes)

- **Render page:** a `#/render/<version_id>?views=iso,front,top&tags=1` page
  shows only the viewport and sets `window.__ready` once the scene is loaded.
- **Service:** `app/render.py` keeps a small pool of headless Chromium pages
  through Playwright. It screenshots each view and stores the result in
  `renders`.
- **Users:**
  - the `render` and `critic` tools;
  - library card images, replacing today's upload from the browser.
- It uses the same viewer users see, so the agent judges exactly what people
  will see.

## Workstreams (six people)

| # | Stream | Owns | Depends on |
|---|---|---|---|
| 1 | **Data & jobs** | schema, Alembic, repository layer, JSON-library migration, job runner (lease / heartbeat / resume), jobs API + SSE | none (start first; publish models on day 1) |
| 2 | **Agent harness** | tool registry and types, Muse tool loop, budgets, checkpoint/resume through `agent_steps`, stop rules | 1 (models), stubs for 3/4/5 |
| 3 | **CAD components** | parametric CadQuery contract, sandbox changes, measurement, component library and reuse, removing the fixtures, CAD verifier checks | 1 |
| 4 | **Renderer & critic** | render page + Playwright service, `render` tool, critic prompt and schema, visual-quality metrics, card images | 1 |
| 5 | **Memory & evaluation** | memories store and retrieval, writing lessons from reviews / critic / failures, batch eval on the database (with and without memory), gate audit | 1, 2 |
| 6 | **Frontend & API** | live run trace, jobs UI, library and version history from the database, review writing to `reviews`, keeping e2e green | 1 |

**Fix these interfaces on day 1** so the streams can work in parallel:
- **`Tool`:** `name`, `description`, `input_schema`, and
  `run(ctx, args) -> ToolResult{text, images, data}`.
- **Repository functions** for every table (stream 1 publishes signatures
  first).
- **Renderer:** `render(version_id, views) -> list[RenderImage]`.
- **Component:** `build(p: dict) -> cq.Workplane`, plus `param_schema`.
- **Job payloads:** one per job kind.

## Milestones and acceptance tests

| Milestone | Accepted when |
|---|---|
| M1 Data & jobs | `kill -9` the worker mid-job and the job resumes from its last step; all current tests pass on the database; the JSON library is migrated |
| M2 Agent sees its work | the agent builds both of our own drawings (`scripts/demo_drawing.py`) end to end, renders and critiques its work, and ends with 0 FAIL; the trace is visible in the UI |
| M3 AI-made objects | no hand-modelled generator left in `REGISTRY`; WC, vanity and basin come from AI CadQuery with measured dims that match their params |
| M4 Memory | a reviewer correction in run 1 changes run 2's output (A/B on the eval set) |
| M5 Evaluation | the 24-drawing batch rerun on the agent, compared with v2: built %, FAIL, values traced, critic verdicts, tokens and time |

## Risks

- **Images in tool results** must come back as a follow-up message; this is
  the spike above.
- **Agent loops cost more tokens.** Budgets apply per run; prompt caching
  helps (seen 29 Oct).
- **Sandbox throughput.** Modal concurrency limits apply, and component reuse
  cuts repeat CAD runs.
- **Headless Chromium on servers** needs the Playwright image wherever workers
  run.
