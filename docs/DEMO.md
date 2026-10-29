# Demo script (3 min)

**Rule for every shot: never show a third-party manual page or drawing on
screen.** Use your own drawing for anything where the source drawing is visible:
the upload, the drawing panel, and the review screen. For details generated from
third-party documents, keep the drawing panel closed and show only the 3D. The
hosted demo enforces this (source drawings are not served).

**Our own drawings:** `python scripts/demo_drawing.py` writes
`data/demo/setout-demo.pdf` with two pages:
- page 1, a shower step-down section;
- page 2, a bathroom elevation.

Every printed number is read from `ncc/rules/rules.json`: the 25 mm step-down,
the 1800 mm membrane height and the 150 mm water-resistant zone above the
basin. Every other dimension comes out as an assumption.

Show the SAM link on page 2. SAM finds nothing in a flat section, but on the
elevation it locates the WC (0.88), the vanity (0.61) and the shower rose
(0.62); click the WC box and the camera flies to the WC.

Before recording: generate your own detail once, so the 1–3 minute generation can
be shown as a short cut rather than live. Warm up Modal (one CAD run) and the
guide (one question).

| Time | Shot | Say |
|---|---|---|
| 0:00–0:20 | Title, then the library | "Waterproofing is the most common serious defect in NSW apartments: 63% of serious defects in a study of 492 buildings. The right detail exists — in a PDF nobody on site reads." |
| 0:20–0:40 | Library: *By situation*, then *By component* → membrane → a card | "Every detail here was generated from a drawing. Browse by where you are, or by product: where does this membrane go?" |
| 0:40–1:10 | **Add from PDF** → your own drawing → **Generate 3D with AI** → cut to the build-up animation | "Meta's Muse reads the drawing — every label, every printed dimension — then writes a detail program. It never writes geometry: our compiler does, from the printed numbers." |
| 1:10–1:30 | The self-check result panel (issues found, *correction accepted*) | "Then it checks its own work: it renders the 3D, compares it with the drawing, and fixes what's wrong. A fix is only accepted if the verifier says nothing got worse." |
| 1:30–1:55 | **Review**: printed values first → click one → its parts isolate; click a box on the drawing panel → the camera flies to that part | "An expert reviews before anything is published. Every printed value shows its exact quote and the parts it drives. SAM 3.1 links each part to where it is on the drawing." |
| 1:55–2:30 | Guide: *Walk me through the install* → camera moves, parts isolate, install steps play | "Then anyone on site can ask. The guide drives the 3D while it explains — and every number it says is checked against the detail. If it invents one, it's flagged." |
| 2:30–2:45 | A part → **Model with AI (CadQuery)** → the perforated grate | "For shapes our grammar can't express, Muse writes CAD code that runs in an isolated Modal sandbox with no network. Always labelled illustrative." |
| 2:45–3:00 | Back to the library; the live link | "Any installation manual becomes an interactive, traceable 3D guide. Muse and SAM do the reading; code does the checking." |

**Numbers to have ready** (from `docs/EVAL.md` and the README): the batch
evaluation result, the guide's grounding rule, and the NCC benchmark (35% cold,
100% grounded, n=31) if asked.

**Do not claim**: compliance verdicts, measurement of real installations, or
product approvals. Setout shows what correct looks like.
