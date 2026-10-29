# Playground v2 plan — generative installation playground

Written 29 Oct 2026.

> **Superseded for the AI side by `docs/PLAN_V3.md`** (proposed 29 Oct): an agent
> harness with tools, a database, persistent jobs, memory, AI-written CAD objects
> and a renderer the agent can see.

## The product

Manufacturers sell to us; their customers use the playground.

- **In:** a manufacturer's installation documents (PDF drawings, data sheets) plus code rules.
- **Out:** an interactive 3D library of installation details. You browse it **by situation**
  (residential › balcony › door sill) or **by product** (where does this
  membrane go, and how). Every dimension is traceable to its source.
- **AI everywhere:**
  1. **Reading.** A Meta Muse vision model reads a drawing and writes a *detail program*.
  2. **Generating.** A deterministic compiler turns the program into 3D. The model
     never writes geometry.
  3. **Guiding.** A sidebar agent drives the viewer: flies the camera, isolates parts,
     steps through the install, and cites the source for every number.

The chat is one feature. The engine and the library are the product.

## Why the current engine can't scale

Today a detail is a hand-written seed: `app/seed.py` places every vertex in absolute mm.
Changing `step_height` doesn't move the membrane, and a model can't reliably emit
hundreds of coordinates. "Any construction detail" needs a **grammar**, not more seeds.

## Status update — 29 Oct evening

AI generation moved to the front after a user test showed the app couldn't
generate 3D from any uploaded document. The grammar shipped in a simpler, more
general form than sketched below: **named params + parts from a closed shape
set whose dims are expressions of params** (`app/program.py`). That covers
rooms, sections and assemblies with one compiler. Anchors and layer stacks
remain possible as macros later. The extract pipeline (read → compose → gates →
build) is live: `app/generate.py`, plus the "Generate 3D with AI" button.

## Core idea: a detail grammar (spec v2)

Almost every installation detail is published as a **2D section**: sills, hobs,
upstands, flashings, slab edges, cladding junctions, roof edges, window heads. One
grammar covers all of them:

| Part of a detail | Grammar construct | Example |
|---|---|---|
| Dimensions | `params`: named values with provenance (drawing / code rule / manufacturer doc / user / assumption), source quote, min/exact/max | `step_height = 100, min, "100mm minimum step"` |
| Fixed construction | `substrates`: closed profiles whose vertices are **expressions of params** | lower slab top at `-step_height` |
| Reference points | `anchors`: named points (expressions) | `step_edge = (0, -step_height)` |
| Applied layers | `layers`: follow a chain of anchors or a substrate face, with thickness, side and order. Each layer offsets from the one before (primer → membrane → screed → adhesive → tile) | membrane follows `upstand_top → step_edge → slab_far` |
| Discrete parts | `parts`: closed-set generator (bead, angle, rod, post, fillet, wedge, box) placed **at an anchor** | sealant bead at `gap_centre`, width `gap_width` |
| Sequence and logic | `install_steps`, `relations`, `unresolved`, `options` (surface/substrate swaps) | "membrane before packer" |
| Where it applies | `situation` (sector › area › element), `products` | residential › balcony › door sill |

A **compiler** in `app/` evaluates the expressions with a whitelisted arithmetic
evaluator, resolves anchors, offsets the layer stacks and places the parts. It emits
the existing `Component` list, which the existing closed-set generators turn into
meshes, followed by the existing verification. Derived coordinates carry
"derived from step_height (drawing)", so provenance flows through.

The model emits programs in this closed vocabulary (a json_schema), never code. That
keeps the core invariant: geometry is generated from source numbers.

3D-only cases (internal corners, pipe penetrations) come later as extra frame types;
the section frame covers the bulk.

## Workstreams

All four carry equal weight.

### A. Engine: grammar + compiler
- `app/program.py`: Pydantic `DetailProgram` (v2), the expression evaluator, anchors,
  layer offsetting and part placement. It compiles to `DetailSpec` v1, so geometry,
  verification and export are reused.
- Rule provenance: add `code_rule` to `Provenance` and an optional `rule_id` to `Param`,
  so the NCC rules plug in.
- Rewrite D3 and D4 as programs and prove parity with the v1 verification results.
- Port the NCC hob and wall–floor junction. Write new programs (balcony upstand, window
  sill, slab-edge flashing) from public sources, paraphrased and cited.
- Generalise `verify.py`: check every `drawing` / `code_rule` param through the program,
  replacing the hard-coded checks on `slab_lower`, `sealant` and `screed`.

### B. Library + selector (the front door)
- The library is the built-in programs (committed, our own paraphrased transcriptions)
  plus published programs under `var/library/`.
- `GET /api/library`: a situation tree and a product index, both derived from the programs.
- Frontend: landing page with Residential / Commercial › area › element › detail, a
  **By product** tab, deep links (`#/d/<id>?finish=…&substrate=…`), and option swaps.
- The current workbench stays reachable at `#/workbench` (keeps `e2e/drive.py` green).

### C. Visuals: product-quality, generated in code
- **Procedural PBR textures,** drawn on a canvas by our code (own work): concrete,
  screed, primer, liquid membrane, sheet membrane, tape, combed adhesive, tile + grout,
  sealant, timber, aluminium, steel, fibre cement, insulation, gravel/backfill,
  geofabric, drainage cell. A material class is chosen by component role.
- **UVs:** box-projected from mm positions on the client, so tile and adhesive
  patterns are true scale.
- **Lighting:** `RoomEnvironment` image-based lighting, ACES tone mapping, sRGB output,
  soft shadows, and room context (floor and walls around the detail).
- **Labels and hotspots:** CSS2D labels with leader lines, and a clickable hotspot
  per part.
- **Camera:** a controller with a target, plus tweened `flyTo(component)`,
  `isolate(ids)` (others ghosted), view presets, and the bottom-up "build"
  intro animation in install order.

### D. AI pipeline (Meta Muse via the existing `/responses` client)
- **Shared client** `app/meta_client.py`, lifted from `ncc/extractor/extract.py`:
  streaming, json_schema, `input_image`, tool calls.
- **Extract.** Upload a PDF, pick a crop, then Muse (vision, json_schema = DetailProgram)
  produces a candidate program. Deterministic gates follow:
  - it must compile;
  - every `drawing` value needs a source quote containing that number;
  - labels must be verbatim;
  - anything else becomes an assumption.

  Then a review screen shows the crop beside the 3D with accept/edit, and *publish*
  adds it to the library. Demo targets: Conpro D1/D2 (not yet transcribed) and a new
  manufacturer sheet.
- **Guide agent** (sidebar, Muse tool calling):
  - Tools: `focus`, `isolate`, `show_all`, `explode`, `section`, `play_step`,
    `select`, `set_view`, `open_detail`.
  - The backend runs the loop and validates arguments; the client plays the
    narration and actions on a timeline.
  - **Grounding verifier:** every number in a reply must exist in the program's
    params, otherwise one retry with feedback. This is the benchmark's verifier-retry
    idea, now live.
  - A "Walk me through the install" button generates a full tour.

## Milestones (in order)

| # | Deliverable | Gate |
|---|---|---|
| 1 | This plan. Viewer foundation: camera controller + flyTo/isolate API, procedural material library, lighting | Existing details render textured; `npm test`, e2e green |
| 2 | Engine: program schema, compiler, D3/D4 as programs (parity), NCC hob + wall–floor ported | pytest parity: same PASS/FAIL as v1 |
| 3 | Library API + selector UI (both entries), labels/hotspots/intro animation, room context | Browse → open → explore 5+ details |
| 4 | Meta client + extraction pipeline + review/publish UI | D1 or D2 extracted by Muse, compiles, verifies, published |
| 5 | Guide agent + grounding verifier + guided install tour | Tour on 3 details with 0 ungrounded numbers |
| 6 | Polish: phone layout, PDF/report export, README, end-to-end run | Full path green |

## Rules that still hold

- Geometry comes only from the compiler plus closed-set generators. The model writes
  programs, never geometry.
- Never invent a number, label, product or approval. Unquoted values are
  assumptions, visibly.
- Never commit source documents. Built-in programs are our own paraphrased
  transcriptions with citations.
- Out of scope: pixel→mm measurement, compliance verdicts on real work, model
  training, IFC/BIM, accounts.
- Camera/AR (Jarvis, SAM) comes last.
