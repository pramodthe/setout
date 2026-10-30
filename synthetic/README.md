# Synthetic drawings: templates and styles

Library data for `app/synthetic.py`, the synthetic data engine that turns
parametric section templates into rendered drawings with exact instance masks
(`tools/make_seg_dataset.py` writes the COCO datasets under `data/`). The
engine reads these files; it never names a part.

Everything here is in our own words and is not taken from any drawing,
manufacturer detail or standard. Ranges are **synthetic sampling ranges, not
standards**, for the owner to calibrate against their own drawings; a sampled
value becomes an `assumption` in the program it writes, and every rendered
sheet prints `SYNTHETIC SAMPLE`. Files stay `status: draft` until the owner
reviews them.

## Templates (`templates/*.yaml`)

A template is a v2 detail program with randomness. Coordinates are mm; the
expression grammar is `app/program.py`'s (numbers, names, `+ - * /`, `min`,
`max`).

| key | meaning |
|---|---|
| `id`, `version`, `status`, `weight` | identity; `weight` is the share of drawings drawn from it |
| `assembly_class` | the ontology assembly it draws, or null |
| `choices` | discrete variants, `{id, options: {option: weight}}`; the flag `<id>_<option>` is 1 for the option drawn, else 0 |
| `params` | in order: sampled (`range`, optional `step`, `dist: uniform / log_uniform`, `unit: mm / ratio`, and `basis` saying it is a synthetic sampling range, which the loader requires) or derived (`expr`) |
| `parts` | `id`, ontology `class_id`, `shape` (box, sheet, wedge, profile, fillet: exact primitives only), `dims`, `path` (points `[x, y]` or `{x, y, when}`), or `circle: {cx, cy, r, n}` for a profile; optional `when` (present when > 0; may read `has_<part>` of parts above it) and `p` (probability) |
| `constraints` | expressions that must be >= 0, else the sample is drawn again |
| `relations` | `[source, kind, target]`, kinds from `ontology/relations.yaml`; kept when both parts are present |
| `labels` | callout words per class id |
| `titles`, `notes` | title and note text a sheet may carry |
| `levels` | `{y, text: [...]}` level lines a sheet may draw |
| `breaks` | `{x}` or `{y}` (with optional `when`) where the drawing cuts parts off; a break line is drawn there |

Order of evaluation: choices, sampled params, parts (presence), derived
params, constraints, then each present part's geometry. Parts must not
overlap; the engine rejects a sample whose masks share pixels.

## Styles (`styles/*.yaml`)

Generic drafting conventions, not the owner's house style. Sizes are paper
millimetres; the engine converts them with each drawing's paper scale. A list
is picked from uniformly, a mapping by weight, a two-number list is a range.

`scan_dpi`, `nts` (chance of true scale, else an x/y stretch range), `text`
(height, fonts, upper-case share), `weights` (named lineweights), `hatch_weight`,
`linetypes` (paper-mm dash patterns), `default` and `classes` (per class id:
`weight`, `linetype`, `hatch` as `[ISO pattern, scale low, scale high]` or
`none`, `angle`), `thin` (how sheet-shaped parts are drawn: `outline`, `line`,
`dashed_line` or `filled`, with `max_line_px`), `labels` (share labelled,
sides, leader ends), `dims` (arrow blocks, sizes, chains, extra width
dimensions) and `clutter` (title, notes, break lines, level lines).

## Adding a family or a style

Add a YAML file; no code changes. `tests/test_synthetic.py` loads every file,
samples and cuts each template, and checks that the engine source names no
class or part.
