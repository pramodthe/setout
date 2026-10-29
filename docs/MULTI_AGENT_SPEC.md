# Setout multi-agent spec

Proposed 29 Oct 2026. This spec replaces the single agent loop in `app/agent.py`
with a team of specialist agents run by LangGraph. Everything else in
`docs/PLAN_V3.md` stays: the database, persistent jobs, versions, tools, honesty
gates, renderer, CAD contract and memory. Read that plan first.

**Status: spec only. No code has been written for it yet.**

Decided by the project owner on 29 Oct 2026:
1. **Harness:** LangGraph as the orchestrator, with LangChain `create_agent`
   for each specialist.
2. **Model:** Muse is called through `langchain-meta`'s `ChatMetaModel`.
3. **Materials:** closed-set material programs only (the owner left this to
   the spec; the reasons are under "Materials").
4. **Rollout:** the single v3 agent stays behind a switch until the A/B
   evaluation (G5) shows the graph is at least as good.

It also adds one new capability: **agents write the materials**. Every part gets
a texture that an agent wrote as a material program, and our code renders it
to image files. Today every detail shares the same hand-written textures.

## Why

The v3 harness is one Muse agent that does every job in one conversation:

- **Reading, composing, CAD, judging and fixing all share one context and one
  budget.** A long CAD repair crowds out the program, and one bad turn can stall
  the whole run.
- **Nothing runs in parallel.** Five CAD objects are modelled one after another.
- **No agent makes materials.** `web/src/materials.ts` has 22 hand-written
  material classes. Every detail looks the same, and the GLB has flat colours,
  no textures and no UVs.
- **Fixes aren't routed.** A critic issue about one object goes back to the
  same generalist that wrote everything.

The goal is a pipeline where each agent owns one kind of output, and they run
in parallel where they can. The critic is independent. Code decides the order,
the budgets and when the run is done.

## The rules that do not change

- **The gates are the honesty boundary.** They stay inside `write_program` /
  `patch_program` (`app/agent_tools.py`), so no agent can skip them. A drawing
  value needs its quote from this drawing. Labels are verbatim.
- **Geometry comes from named values** (PLAN_V3 "The core rule"):
  - layers are exact grammar primitives;
  - objects are parametric CadQuery in the no-network Modal sandbox, measured
    against their envelope.
- **Materials are appearance only.** A material never carries a verified
  dimension and never produces a PASS. See "Materials" for how pattern sizes
  stay traceable.
- **Finishing is decided in code.** A run is `done` only when the latest
  version has 0 FAIL and the critic passed that exact version and its
  verification. Anything else ends as `needs_review` with reasons.
- **No product or brand is invented.** That covers materials too: no "looks
  like Brand X" and no branded colour names. `ProductRef.ref` stays `null`.
- **Trade-agnostic.** No role, prompt or material class assumes waterproofing.

## Harness choice

| Option | What it is | Verdict |
|---|---|---|
| **LangGraph 1.2** `StateGraph` | Explicit graph: nodes, conditional edges, parallel fan-out (`Send`), Postgres/SQLite checkpointer, interrupts | **Use as the orchestrator** |
| **LangChain 1.4** `create_agent` | A tool-calling agent that compiles to a LangGraph graph. It has middleware for call limits, retries and human review, and structured output | **Use for each specialist agent** |
| **Deep Agents 0.7** `create_deep_agent` | `create_agent` plus a virtual filesystem, LLM-delegated subagents (`task`), skills and sandboxes | **Not now** (reasons below) |

**Decided:** LangGraph as the orchestrator, `create_agent` for each specialist.

Why not Deep Agents:
- **The LLM picks which subagent runs and when it's done.** Our order, budgets
  and finish rule would have to be forced back on it through middleware. A
  graph puts them in code from the start.
- **Its virtual filesystem duplicates what we already have:** immutable
  versions, components and artifacts in the database.
- **It is pre-1.0** (0.7.32, weekly releases).
- **It pulls in other providers' SDKs.** It hard-depends on
  `langchain-anthropic` and `langchain-google-genai`, and with `model=None` it
  falls back to an Anthropic model.

Deep Agents is built on the same `create_agent`, so a specialist can move to it
later without changing the graph. A good candidate is an open-ended research
agent.

Not used: LangSmith, LangGraph Platform and Agent Server. They are paid, and
they send traces off our servers. Our own `agent_steps` table stays the trace.

## The team

| Agent | Kind | Owns | Tools | Returns (structured) |
|---|---|---|---|---|
| **Reader** | `create_agent`, no tools | what the drawing says | none (image in) | labels, printed values with exact quotes, drawing type, rotation (today's READ pass) |
| **Composer** | `create_agent` | the detail program: params, parts, roles, layers, CAD envelopes and bindings, install order | `recall`, `write_program`, `patch_program`, `build_and_verify` | version id, object parts, material slots |
| **Modeller** ×N | `create_agent`, one per object part | CadQuery for one object | `recall_components`, `author_component`, `reuse_component` | component id, measured envelope |
| **Material artist** ×M | `create_agent`, one per material slot | one material program | `recall_materials`, `write_material`, `preview_material` | material id |
| **Critic** | `create_agent`, no tools | an independent judgment | none (drawing + renders + check summary in) | verdict, issues with an owner |
| **Curator** | `create_agent`, no tools | lessons for memory | none | proposed memories |

The reader, critic and curator are single structured calls. They are still
built with `create_agent` (no tools, `response_format` set) so that **every
Muse call passes through the same middleware**: trace, budget, replay and
image references. No code calls the model directly.

Code nodes, not agents: `prepare`, `bind`, `verify`, `render`, `route` and
`finish`. The verifier is never an LLM.

**The critic stays independent.** It gets the drawing, the renders and the
verification summary. It never sees the authors' messages or reasoning, so it
is not an author grading its own work. Each issue names an owner:
`program`, `component:<part_key>` or `material:<slot>`. That is what lets
`route` send the fix to the right agent.

## The graph

```
START
  │
  ▼
prepare ─ load job, figure, recalled memories              (code)
  │
  ▼
reader ─ READ record                                       (1 Muse call)
  │
  ▼
composer ⇄ gates → compile → verify                        (agent)
  │
  ├── Send × object parts ──► modeller[part]               (agents, parallel)
  └── Send × material slots ─► material_artist[slot]       (agents, parallel)
  │
  ▼
bind ─ one successor version: components + materials       (code)
  ▼
verify ─ build_and_verify                                  (code)
  ▼
render ─ iso / front / top, part tags                      (code, headless viewer)
  ▼
critic ─ verdict + issues with owners                      (1 Muse call)
  ▼
route                                                      (code)
  ├─ 0 FAIL and critic pass ───────────────► curator → finish(done)
  ├─ issues, rounds left, budget left ─────► repair: Send each issue group to
  │                                          its owner (composer / modeller[part]
  │                                          / material_artist[slot]) → bind → …
  └─ otherwise ────────────────────────────► finish(needs_review, reasons)
```

**Parallel branches never create versions.** Versions are immutable, and each
has one parent. If two modellers each created a successor of the same version,
they would conflict. So:
- `author_component` and `write_material` store a component or material and
  return its id. They do not touch the version.
- `bind` is the only node that applies components and materials. It creates
  one successor version in one step.
- Only the composer edits the program. A modeller that needs a new param or a
  different envelope reports an issue for the composer. It never edits the
  program itself.

This changes one tool. Today `model_component` writes the CAD *and* binds a
successor version. It splits into `author_component` (no version) plus the
binding step in `bind`. `reuse_component` changes the same way.

**Repair rounds:**
- At most `repair_rounds` (default 3).
- A repair is kept only if `quality()` doesn't drop, the same rule as the v2
  critique. Otherwise `route` reverts to the better version and ends the run
  as `needs_review`.

## State

The graph state holds **ids only**. Programs, components, materials, renders
and checks live in the database. The database stays the source of truth, and
checkpoints stay small.

```python
class RunState(TypedDict):
    job_id: str
    detail_id: str | None
    figure_id: str
    read_id: str | None
    version_id: str | None              # latest bound version
    verification_id: str | None
    render_ids: list[str]
    critic_verdict_id: str | None
    components: Annotated[dict[str, str], merge]   # part_key -> component_id
    materials: Annotated[dict[str, str], merge]    # slot -> material_id
    issues: list[Issue]                 # open critic/verifier issues with owners
    round: int
    outcome: Literal["running", "done", "needs_review"]
```

`merge` is a reducer, so parallel branches add their results without
overwriting each other.

## Durability: jobs, checkpoints and exactly-once effects

There are two layers, and each keeps its own job:

1. **Our jobs table** (PLAN_V3 "Persistent jobs"). It is unchanged: lease,
   heartbeat, fence and retries. Open-source LangGraph has no run queue, so we
   keep ours.
2. **The LangGraph checkpointer** tracks where the run is in the graph:
   - `PostgresSaver` in deployment, `SqliteSaver` in tests;
   - `thread_id = job_id`;
   - `durability="sync"`, so every step is written before the next starts.

**Resume:**
- A worker that leases an unfinished job calls
  `graph.invoke(None, {"configurable": {"thread_id": job_id}})`.
- A job with no checkpoint starts with its initial input.
- Already-finished parallel branches are not re-run.

**Inside a node, a crash re-runs the node from its start.** That is safe only
because effects are idempotent:
- **Every model call** passes through the `Checkpoint` middleware (see
  "Model"). A completed call with the same operation key returns the stored
  response, with no new provider request and no new charge. Keys are
  deterministic: `"{role}/{task_key}/turn-{n}"`, where `n` is the number of
  model replies already in the agent's messages. This is the same replay the
  v3 `ModelGateway` does, moved into middleware.
- **Every tool effect** goes through `repo.operation(fence, call_id, …)`, which
  exists in v3. A replayed call id returns the stored result, with no second
  Modal run or render.

So a re-run specialist replays its stored turns and continues where it stopped.

**Human review stays outside the graph.** We don't use `interrupt()`: on
resume LangGraph re-runs the whole node, and a review can take days.
`needs_review` ends the run. A reviewer's edit or a revise request is a new
`revise_detail` job that starts from that version (PLAN_V3).

**Checkpoint tables** (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
`checkpoint_migrations`) are created by `PostgresSaver.setup()`, not by
Alembic. `migrations/env.py` must exclude them from autogenerate.

## Model: `ChatMetaModel` from `langchain-meta`

**Decided:** Muse is called through `langchain-meta` 0.5.0
(`from langchain_meta import ChatMetaModel`; MIT, published by Meta,
subclasses langchain-openai's `BaseChatOpenAI`, Responses API by default).

### Configuration

One factory, `app/agents/models.py`, builds every model instance. No other
code constructs `ChatMetaModel`.

| Setting | Value | Why |
|---|---|---|
| `model` | `meta_client.default_model()` (`MODEL_API_MODEL`, else `META_MODEL`, default `muse-spark-1.3`) | one source for the model name, as today |
| `api_key`, `base_url` | read from our `.env` through `meta_client._env()` and passed explicitly | the package reads `MODEL_API_KEY` / `MODEL_API_BASE`; our `.env` uses `META_API_KEY` / `META_API_BASE` |
| `store` | `False` | Meta keeps responses on its servers by default; we keep our own trace |
| `include` | `["reasoning.encrypted_content"]` | with `store=False`, reasoning must travel with the conversation for the next turn |
| `max_retries` | `0` | no hidden retries: each dispatch is reserved and recorded, as in v3; retries are the job's decision |
| `streaming` | `False` | matches today's non-streaming calls and gives usage in one response |

### Rules for using it

- **Structured output is always `ProviderStrategy`** (Responses `json_schema`,
  strict), set explicitly in `response_format`. Never `ToolStrategy`: it
  forces `tool_choice="any"`, and Meta returns HTTP 400 for anything but
  `"auto"`. The package's model profile covers only `muse-spark-1.1`. For a
  model with no profile, `create_agent` would pick `ToolStrategy` by itself,
  so the explicit setting is required.
- **Tool choice stays `"auto"`.** No agent forces a tool call.
- **No recursive JSON schemas.** Meta rejects them, and every schema stays flat.

### What the v3 `ModelGateway` did, now in middleware

`ChatMetaModel` calls Meta directly, so it bypasses the v3 `ModelGateway`. The
gateway's four jobs move into `create_agent` middleware
(`app/agents/middleware.py`). Every agent gets all four, in this order:

1. **`Checkpoint`** (`wrap_model_call`):
   - before the call, it looks up the operation key
     `"{role}/{task_key}/turn-{n}"`;
   - if a completed step exists, it returns the stored reply without calling
     Meta;
   - otherwise it writes the request to `agent_steps` first, then writes the
     reply after the call.
2. **`RunBudget`** (`wrap_model_call`):
   - before the call, it reserves an estimate of tokens and one model call
     against the run (`repo.reserve`), and caps output tokens to what is left;
   - after the call, it settles the reservation with the reply's real usage
     (`repo.settle`);
   - an exhausted budget raises `BudgetExceeded`, and the run ends
     `needs_review`.
   - It uses the same run accounts as the single agent, so both harnesses are
     measured the same way.
3. **`RoleBudget`** (`wrap_model_call`): checks the role's share of the run
   budget (see "Budgets").
4. **`ArtifactImages`** (`wrap_model_call`):
   - tools and nodes put images in messages as `artifact:<id>` references;
   - just before the call, this middleware swaps each reference for a
     `data:` URL, as the gateway does today.
   - Checkpoints and the trace therefore hold ids, not megabytes of base64.

The single-v3 harness keeps using `ModelGateway` unchanged until rollout ends.

## Budgets

| Level | Limit | Enforced by |
|---|---|---|
| Run | tokens, model calls, tool calls, CAD runs, wall-clock | `RunBudget` middleware against the run's repository accounts; tool and CAD limits in `repo.operation`; wall-clock via the job fence |
| Role | a share of the run's tokens and calls | `RoleBudget` middleware; LangChain has no token-budget middleware |
| Agent instance | model calls, tool calls | `ModelCallLimitMiddleware(run_limit=…)`, `ToolCallLimitMiddleware(run_limit=…)` |
| Graph | steps | `recursion_limit` set explicitly; the docs and the 1.2.18 source disagree on the default |
| Repair | rounds | `route` (default 3) |

Starting values, to be tuned with the evaluation:

| Role | Model calls | Tool calls |
|---|---|---|
| composer | 12 | 30 |
| modeller, per part | 6 | 6 |
| material artist, per slot | 4 | 4 |
| critic, per round | 1 | n/a |

Hitting any limit ends the run as `needs_review` with the reason, never as a
silent stop.

## Materials

### Material programs

Materials follow the same pattern as geometry. The model writes a structured
program from a **closed set** of pattern kinds, and our code renders it. The
kinds are the paint passes `materials.ts` already uses, plus `brick` and
`planks`.

```json
{
  "slot": "wall_tile",
  "class": "tile",
  "label": null,
  "base_color": "#cdc3b3",
  "roughness": 0.32, "metalness": 0, "opacity": 1,
  "seed": 7,
  "noise": {"amplitude": 0.07, "octaves": [4, 16]},
  "pattern": {"kind": "grid",
              "cell_mm": [{"param": "tile_w"}, {"param": "tile_h"}],
              "joint_mm": {"param": "grout_joint"},
              "joint_color": "#8d877d", "stagger": 0},
  "relief": {"strength": 0.8}
}
```

- **`class`** is one of the existing material classes: `concrete` … `glass`,
  `generic`. Adding a class is a code change.
- **`pattern.kind`** is one of: `none`, `speckle`, `streaks`, `fibres`,
  `ridges`, `grid`, `brick`, `planks`, `grain`, `brushed`, `pebbles`, `weave`.
- **`label`** is a verbatim drawing label or `null`, like every other label.
- **`slot`** is a material slot declared by the composer. Each part names its
  slot, so parts made of the same material share one material.

### Keeping pattern sizes traceable

A tile grid, a joint width or a plank width reads as a dimension, so it must be
one:
- **Every size in `pattern` is a `{"param": name}` reference** to a spec param.
  That param carries provenance like any other: `drawing`, `assumption`, and so
  on. Literal millimetres are rejected.
- **The verifier reports each referenced param.** A drawing param shown in a
  material is checked the same way as the geometry: it must be the printed
  value. An assumption stays an assumption.
- **Colour, roughness, noise and relief are appearance.** They are never
  checked and never PASS.

### Rendering

- **`app/materials.py`** renders a material program into three tileable PNGs:
  colour, normal and roughness/metalness. They are 1024², deterministic for a
  given seed, and saved as content-addressed artifacts. It's a port of
  `materials.ts` to Python with numpy and Pillow, so the viewer and the GLB
  match.
- **GLB:**
  - `geometry.py` adds `TEXCOORD_0`, using box-projected true-scale UVs, the
    same mapping as `web/src/uv.ts`;
  - it embeds the three maps per material (`baseColorTexture`,
    `normalTexture`, `metallicRoughnessTexture`).

  Today the GLB has flat colours and no UVs.
- **Viewer:** `viewport.ts` loads the maps for each part's material. The
  hand-written `materials.ts` classes stay as the fallback for parts with no
  material.
- **`preview_material`** renders a swatch on a 300 × 300 mm tile and returns
  it to the material artist as an image.

### Library and reuse

- **New table `materials`:** `id`, `class`, `program`, `program_sha`,
  `artifact_ids`, `status` (`draft` / `reviewed`), `created_by_run` and `uses`.
- **Reuse first:** the material artist calls `recall_materials` and reuses a
  matching material before it writes a new one. Reuse binds the material's
  size params to this detail's params, so nothing carries over from another
  drawing.
- **Review:** a human promotes a material to `reviewed`, as with CAD
  components.

**Decided: closed-set material programs only.** Free-form texture code is out.
That would be Python, written by the model, that draws PNGs in the Modal
sandbox. Why the closed set is better here:
- **Traceability.** Every size in a material program is a param reference the
  verifier can read. Sizes buried inside free-form code can't be checked that
  way.
- **The closed set already covers every material class we have.** The 22
  classes in `materials.ts` are all drawn from these pattern kinds.
- **Cost and speed.** A material program is one small JSON object and renders
  locally in milliseconds. Free-form code needs a sandbox run per texture,
  and a second sandbox path to secure and test.
- **Reuse.** Programs are easy to compare and recall by class and pattern.
  Code isn't.

If a material ever needs a pattern the set can't express, the fix is a new
pattern kind in `app/materials.py`. That is a code change, just as a new shape
is a new generator.

## Memory

Memory works as in PLAN_V3 "Memory", with one addition: the **curator** runs
after a run and proposes lessons from accepted repairs. Each lesson has a scope
and an owner role, so the composer recalls composer lessons and the modeller
recalls CAD lessons. Code deduplicates the lessons and writes them.

**A memory never supplies a drawing value**, and the gates still require the
quote from this drawing.

## Trace and telemetry

- **The `Checkpoint` middleware writes model steps, and `repo.operation` writes
  tool steps**, into the same `agent_steps` table the single agent uses.
- **`agent_steps` gains `agent` (role) and `task_key` columns**, so the UI shows
  one lane per agent. The JobPanel groups by them.
- **`agent_runs` gains `harness`** (`single-v3` | `graph-v1`).
- **No data leaves except to Meta and Modal:**
  - set `LANGSMITH_TRACING=false` and `LANGCHAIN_TRACING_V2=false`, and leave
    `LANGSMITH_API_KEY` unset;
  - `langchain-core` installs `langsmith` as a dependency, but tracing is off
    unless enabled;
  - never call `draw_mermaid_png()` (it posts the graph to mermaid.ink).
- **`LANGGRAPH_STRICT_MSGPACK=true`.**

## Code layout

```
app/agents/
  graph.py         StateGraph: nodes, Send fan-out, route, finish rule
  state.py         RunState, Issue, reducers
  models.py        the one ChatMetaModel factory (settings above)
  tools.py         LangChain tool wrappers over agent_tools.Tools (gates stay there)
  middleware.py    Checkpoint, RunBudget, RoleBudget, ArtifactImages
  roles/           reader, composer, modeller, material_artist, critic, curator
                   (prompt, tools, response schema each)
app/materials.py   material program schema + PNG renderer
```

- **`app/worker.py`** calls `graph.run(job)` when `SETOUT_HARNESS=graph`, and
  the single agent otherwise.
- **`app/agent.py`** stays as `single-v3` for the A/B comparison.
- **The `ncc/` track imports none of this.** It stays stdlib only.

## Dependencies

Add, pinned (versions read from PyPI on 29 Oct 2026; all MIT, all accept
Python 3.14):

```
langgraph==1.2.18
langchain==1.4.7
langchain-core==1.7.1
langchain-meta==0.5.0
langchain-openai==1.7.3   # base class of ChatMetaModel; pinned explicitly
langgraph-checkpoint-postgres==3.2.0
langgraph-checkpoint-sqlite==3.1.2
psycopg-pool              # needed by the Postgres checkpointer; pin at install
numpy                     # material renderer; pin at install
```

Pin the rest of the dependencies `langchain-meta` brings in (the `openai` SDK
among them) at install time.

Not added: `deepagents`, `langchain-modal`, `langsmith` (beyond what
`langchain-core` pulls in).

## Spikes (before building on them)

| # | Question | Accepted when |
|---|---|---|
| S1 | `ChatMetaModel` against `muse-spark-1.3` | a `create_agent` with two tools completes a tool loop; `ProviderStrategy` returns schema-valid JSON; `store=False` with encrypted reasoning carries over to the next turn; `max_retries=0` and `streaming=False` are honoured; the reply carries token usage for `RunBudget` |
| S2 | Images back to the model | a tool returns an image block (resolved by `ArtifactImages`), langchain-openai sends it inside `function_call_output` (Meta's schema allows an image list), and the model describes it correctly in the same turn. If not, fall back to a follow-up user message with the image (today's method) |
| S2b | Middleware replay | `Checkpoint` returns a stored reply without a Meta call, and the agent continues as if the call had happened |
| S3 | Crash and resume | `kill -9` the worker during the modeller fan-out; the job resumes, no Modal run or paid model call repeats, and the final version is the same as an uninterrupted run |
| S4 | Subgraph resume granularity | confirm whether a specialist subgraph resumes mid-loop or restarts and replays through `Checkpoint`; either is correct, but we need to know which |
| S5 | Parallel throughput | 5 modellers and 6 material artists at once stay within the Meta rate and Modal concurrency limits (`max_concurrency` set per kind) |

## Milestones and acceptance tests

| Milestone | Accepted when |
|---|---|
| G1 Graph harness | both of our own drawings (`scripts/demo_drawing.py`) run end to end on the graph; offline tests pass with a fake responder and `SqliteSaver`; S3 passes |
| G2 Parallel specialists | object parts and material slots are built in parallel; one `bind` creates one successor version; no version conflicts in 20 runs |
| G3 Agent-made materials | every part has a material; pattern sizes trace to params; the GLB has UVs and textures; viewer and GLB look the same |
| G4 Routed repair | a critic issue reaches its owner and is fixed; `quality()` never drops across a repair |
| G5 A/B evaluation | the 24-drawing batch on `graph-v1` vs `single-v3`: built %, FAIL, values traced, critic verdicts, materials reused, tokens and time |

## Rollout

**Decided:**
- `single-v3` stays the default behind `SETOUT_HARNESS` until G5 shows the
  graph is at least as good.
- Then `graph-v1` becomes the default. `app/agent.py` and the
  `ModelGateway` dispatch path are removed in a later change, once nothing
  depends on them.

## Workstreams

These fit onto PLAN_V3's six streams:

| PLAN_V3 stream | Adds |
|---|---|
| 1 Data & jobs | `materials` table, step `agent` / `task_key`, run `harness`, checkpointer setup and Alembic exclusion, worker resume via `thread_id` |
| 2 Agent harness | `app/agents/`: graph, state, the `ChatMetaModel` factory, the four middlewares, role prompts; spikes S1, S2b, S3 and S4 |
| 3 CAD components | split `model_component` into `author_component` + `bind`; the modeller role |
| 4 Renderer & critic | critic issues with owners; `preview_material` swatches; spike S2 |
| 5 Memory & evaluation | curator, role-scoped lessons, G5 A/B |
| 6 Frontend & API | per-agent lanes in the run trace; material maps in the viewer; GLB textures |

The material renderer (`app/materials.py`, GLB UVs and textures) is new work.
Stream 4 or 6 can take it.

## Depends on uncommitted v3 code

This spec builds on interfaces in the v3 work that isn't committed yet:
- `Tools.run`;
- `repo.operation`, `repo.reserve` / `repo.settle`, and
  `repo.model_request` / `repo.model_response` (used by the middleware);
- the `ModelGateway` replay and image logic, which the middleware reproduces;
- `stage_verification`;
- `Renderer`.

If those change before v3 lands, update this spec to match.

## Out of scope

The CLAUDE.md cuts stand:
- no model training;
- no pixel-to-millimetre measurement;
- no compliance verdict on real installations;
- no IFC/BIM, accounts or catalogues.

Also out: LangSmith, LangGraph Platform / Agent Server, and Deep Agents for now.
