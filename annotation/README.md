# annotation

Turns a CAD drawing into a labelled segmentation dataset without anyone tracing outlines.
The DXF already holds every shape, and its callouts and hatches suggest what each one is.
A person only checks.

```sh
python annotation/cli.py propose data/drawings/sheet.dxf      # one or a few sheets
python annotation/cli.py batch data/drawings --workers 8      # a whole folder
python annotation/cli.py report                               # what it found, per detail
python annotation/cli.py contact                              # every detail on one page
python annotation/cli.py review                               # http://127.0.0.1:8765/
python annotation/cli.py export --out data/datasets/owner-v1 --project-folders
python annotation/cli.py upload data/drawings/sheet.dxf --detail 7   # one detail for the app
```

One file can hold many details, so a file is split first, and a **detail** is the unit of
review and of the dataset. `batch` carries on past a bad file and leaves alone any file a
person has started. `propose` and `batch` read DXF only, not PDF; convert DWG first. Nothing
calls a model or the network.

## The review page

One detail at a time, with coloured regions over the sheet. Each decision saves as you make
it. When a detail is finished, what you said is carried to the details still open.

| Key | Does |
|---|---|
| Enter | accept the proposal |
| A | accept every proposal with a class on this detail |
| c or / | pick a class by typing; 1 to 9 pick a suggested one |
| x | not a part |
| r | bring back a rejected or set-aside region |
| m, or Shift click | pick regions to merge; m again merges them |
| s | split: click two points along the cut |
| u | undo |
| z, 0 | zoom to the region, to the sheet |
| v, l | show set-aside regions, show names on the sheet |
| [ ] | previous, next sheet |
| f | leave this sheet out of the dataset, with a reason |
| ? | all of this |

Every decision records its active seconds, per region in `sheet.json` and in `events.jsonl`.
The clock stops while the page is hidden, or after 45 seconds with no input. That gives the
real cost per part.

The server binds to 127.0.0.1 and writes only to the store. It needs the page's token on
every write and refuses any other Host. The page has no CDN and no external script.

## Exporting

Output has the same layout as `tools/make_seg_dataset.py`, using its tiling, clipping,
keyholes and COCO writer. `manifest.json` says `"source": "owner"`, so
`training/roboflow_upload.py` refuses it.

Splits are by project, never by sheet or tile. Pass `--project-folders` (the first folder
under the path given to `batch`) or `--projects file.csv` (columns `sheet`, `project`).

Export refuses, and writes nothing, if a sheet has no project, if there are fewer than
three projects, or if one drawing sits in two projects.

The split a project lands in is frozen in `<store>/splits.json` and never moves, so the test
set stays the test set as drawings are added. A detail with undecided regions is left out
whole: a part left unlabelled would teach a model that it is background.

A detail is a row in `drawings.jsonl` and the source of its tiles. All the details of a project
stay in one split, because details repeat with small changes: a split inside a project would
test a model on near-copies of what it trained on.

## How a file is split

Details are found by whitespace: boxes of linework closer than one text height are one
detail. So a frame gathers what it holds, however many groups that is, and an element poking
through a frame stays with it. A rectangle holding most of the sheet is its border and does not
count. A cluster of under five entities joins the nearest detail, or is counted as a stray.

A frame holding several groups stays one detail unless a person marks it, because a gap inside a
frame is often the detail itself: a joint with a flashing across it, or a section drawn with a
break. On the first real file, 7 of 8 such frames were one section, and only one was plausibly
several details. The contact sheet shows these as `x2 frame <handle>`, and `split-frame` lists
them and marks the ones to split:

```sh
python annotation/cli.py split-frame sheet.dxf --store data/annotation/<name>            # list
python annotation/cli.py split-frame sheet.dxf 1F3A 2B07 --store data/annotation/<name>  # mark
python annotation/cli.py propose sheet.dxf --store data/annotation/<name>                # apply
```

The marks stay in `<store>/split_frames.json`, and `--clear` removes them. Pass the same `--store`
to `upload`, so it numbers details as the store does. A marked frame gives one detail per group,
and the frame belongs to none. It must be a closed rectangle, within about half a degree of
square. Groups are told apart by their boxes, so a long L-shaped part whose box bridges two
groups keeps them together, and four loose lines round two groups are not seen as a frame.

A column of at least three swatches with text beside them is a legend, not a detail.

A layout is usually a cover sheet, so the only thing taken from one is a callout: a leader
landing in a viewport that shows some of the drawing. Scale comes from such a viewport, or else
from text height. Blocks are expanded; a block of many tiny pieces is one object.

## How regions are found

All linework goes into one planar arrangement, with text, dimensions, leaders, hatch
pattern lines, leader end markers, break symbols and level lines left out. Its faces are
the atoms.

- A hatch or closed polyline owns the faces inside it, smallest first. A stray line does
  not split it, and a part inside another leaves a hole.
- A face nothing owns is a region if a plain line helps bound it.
- A line left over, bounding nothing, is a thin layer drawn as one line. Its stroke is the
  region.
- Gaps are bridged at free line ends, and points are never moved together. Two sides of a
  thin layer can be closer than any useful gap. A LINE with a dashed linetype has no gaps in
  its geometry; this is for dashes exploded into separate lines.

Rules never delete. A region they doubt is set aside with its reason, and one key brings
it back. Every tolerance is a share of text height, the one thing drawn at a known paper
size, so they hold on not-to-scale sheets. Nothing reads a dimension from geometry.

## How names are proposed

From what the file prints, and from what a person has already said. Each proposal says which.

- **`callout`:** a leader tip landing in a region gives it the text verbatim, mapped to a class
  through the ontology's aliases. LEADER, MULTILEADER and line plus text all count, in
  model or paper space. Short text lying inside a region, with no leader, counts too.
- **`hatch`:** a hatch with the same pattern, scale, angle and shape (thin or compact) anywhere
  in the file gets the class a callout or a person gave one of them. A compact solid fill says
  nothing about what it fills; a thin one is a heavy line, and is a group of its own.
- **`legend`:** a swatch gives its exact hatch the words beside it. Exact, because a file can use
  one pattern at two scales for two things. Those words stay on the region even with no class.
- **`propagated`:** when a detail is finished, a near-identical region in a similar detail gets
  its class. Near-identical is measured: same kind of hatch, same size, the same place once the
  details are lined up, mirrored if need be, and the same shape by overlap. A variant may move
  the wall against the slab, so a second offset lines up what the first left over.
- **No match stays `unknown`.** So does a text naming two classes, a text joining two things
  with a slash, `&`, "or" or "and" even if only one is a class, a hatch whose callouts disagree,
  a hatch two legend rows share, and a hatch the legend gives a meaning other than the callout's.
  The verbatim text and the reason stay on every proposal. A proposal is never a decision: a
  person confirms it, one key per region or one key per detail.

Layer names decide nothing. Linetypes do not name a component either.

## One detail for the app

`upload` renders one detail, numbered as in the store, to the 3.2 MP where the model's image
input stops growing, and writes a PNG, a PDF holding that image losslessly, and a JSON saying
how many pixels the thinnest part got: `good` from 8, `enough` from 4, else `too coarse`. The
PDF goes up through the normal `POST /api/documents`. It holds a picture, and nothing is ever
measured from it.

## Measuring

```sh
python annotation/cli.py measure --sheets 100 --seed 7
```

`app/synthetic.py` knows each part's true shape and class. The pipeline gets only the DXF path,
with layer names scrubbed, and the truth is opened afterwards. There are three variants:
`clean` (as drafted), `lines` (outlines exploded to LINEs) and `rough` (lines plus ends that
miss, real dashes, tips on outlines, real LEADERs). All three are cleaner than real drawings.
Carried names are scored here too, across sheets and on near-copies.

`report` needs no truth, so it runs on real drawings. It counts proposals; it cannot say
they are right. What it says about propagation assumes a person would agree with it.

## Files

| File | What it does |
|---|---|
| `cli.py` | The commands |
| `drawing.py` | DXF to linework, hatches, text and callouts in one frame, through blocks and viewports |
| `details.py` | A sheet split into details, and its legend |
| `planar.py` | Planar faces in numpy: snap rounding, faces, unions, gap bridges |
| `regions.py` | Regions from the arrangement, what is left out, what is set aside |
| `names.py` | Proposals from callouts, hatches and the legend |
| `propagate.py` | Decisions carried to near-identical regions |
| `pipeline.py` | One file from DXF to a record and a render per detail |
| `sheets.py` | The store, its path guard, a reviewer's edits |
| `review.py`, `page.*` | The local server and the page |
| `export.py` | Reviewed sheets to a dataset |
| `upload.py` | One detail as an image, wrapped for the app's upload |
| `measure.py` | Scoring on synthetic truth |
| `report.py`, `contact.py` | What a file came to, with no truth needed; every detail on one page |

## Where the output goes

Everything goes under `data/annotation/` (gitignored), and datasets under `data/`. Inside a
repository any other path is refused; `--store` and `--out` may point outside any repository.
Never commit a drawing, render, mask or dataset.

## Tests

`python -m pytest tests/test_annotation.py -q`. Fixtures are built inside the tests with
ezdxf, with no network. Review-server tests use a socket on 127.0.0.1.
