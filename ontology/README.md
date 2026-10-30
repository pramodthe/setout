# Ontology: library data for the detail world model

What each kind of assembly contains, in our own words. `app/ontology.py`
loads and validates these files; `app/completion.py` fills the required
slots a drawing leaves empty; the verifier checks required slots and class
roles. The first library is the door threshold; a new trade is new files
here, with no code changes.

**Engine code never names a part.** Checks, prompts and schema rules in
`app/` key off `class_id`, roles and slots read from here. Library data in
this folder may name parts, materials and label words (`aliases`).

## Status

Every file carries `status: draft` until the owner has reviewed it, then
`status: reviewed`. Nothing here is a product approval or a compliance
statement. Definitions are our own words. A typical dimension range
(`typical_min` / `typical_max`) is recorded only with the public URL it was
read from, is a prior for placeholders, and is never written as a drawing
value: a placeholder sized from it carries `assumption` provenance and a note
saying the drawing gave no value.

## Files

- `VERSION`: one token, e.g. `2026.10.30`. Appears in every inferred part's
  `inferred_by` (`archetype:<assembly>@<version>:<slot>`).
- `classes/<id>.yaml`: one component class per file; the id matches the
  file name.
- `assemblies/<id>.yaml`: one assembly archetype per file.
- `relations.yaml`: the relation kinds a program may declare, with definitions.

### Component class

| field | meaning |
|---|---|
| `id` | snake_case, unique |
| `name`, `definition` | our own words |
| `function` | one of the spec's closed set: `water_barrier`, `drainage`, `air_seal`, `vapour_control`, `thermal`, `support`, `fixing`, `movement_joint`, `finish`, `protection`, `operation`, `opening_frame`, `datum` |
| `role` | the `NativePart.role` a part of this class takes: `substrate`, `covered_layer`, `finish`, `object`, `zone` |
| `shape_hint` | preferred `NativePart.shape`, or null |
| `form` | `shaped` or `block`, objects only |
| `relations_allowed` | kinds from `relations.yaml` a part of this class may be the source of |
| `dimension_slots` | named sizes: `name`, `unit` (`mm`, `ratio`, `deg`), `typical_min`, `typical_max`, `source` (public URL, required with a range), `derive_from` |
| `drawing_appearance` | measured statistics only (hatch, linetype, relative thickness, with counts); empty until the data engine fills it |
| `aliases` | label words used to match READ labels; never read by engine checks |

`derive_from` is `<slot>.<measure>`: a size taken from a part already in the
program that fills `<slot>` of the same assembly. Measures: `depth` (x-span),
`width` (z-span, along the run), `height` (y-span), `top`, `bottom`, `x0`,
`x1`, `z0`, `z1`. A derived size is an expression over the program's own
parameters, so its provenance follows theirs.

### Assembly class

`id`, `name`, `definition`, `sequence_hints` (free text) and `slots`, each:

| field | meaning |
|---|---|
| `id` | slot id, unique within the assembly |
| `classes` | component class ids the slot accepts |
| `min`, `max` | cardinality; `min` 1 or more only for parts the assembly cannot exist without; `max` null = unbounded |
| `section_visibility` | what a typical section of this assembly shows of the slot: `cut`, `beyond` or `out_of_plane` |
| `placement` | placement family (below), or null |
| `anchor` | the slot whose parts the placement is measured from |
| `position` | the member's place in its opening: `side`, `top` or `infill` |
| `description` | our own words |

### Placement families

The spec's families. Archetype completion (`app/completion.py`) places two
of them in M1 and suggests the rest:

| family | places | reads these dimension slots |
|---|---|---|
| `fill_opening` + `position: side` | one member standing on the anchor's top face at each end of its run | `width` (along the run), `depth` (across; usually `derive_from` the anchor), `height` |
| `fill_opening` + `position: top` | one member spanning the side members, resting on their tops | `height` (its section depth), `depth` |
| `hosted_in_frame` + `position: infill` | the infill between the side members, under the top member, on the anchor | `thickness` |
| `extrude_along_run`, `follow_layer`, `repeat_at_spacing`, `fillet_at_corner`, `posed_object` | suggested only: they need coordinates only the drawing or a reviewer gives | |

A slot the completion cannot place safely (no anchor part, an anchor shape it
cannot read, a size with neither `derive_from` nor a cited range) becomes a
suggestion with the reason, never a guessed part.

## Adding a trade

Add class files for its parts, an assembly file for each archetype, and any
new relation kinds to `relations.yaml` (the program grammar must store the
kind: `NativeRelation.kind`). Keep `status: draft` until the owner reviews.
