# Setout

**A playground for construction details.**

Give Setout a drawing of how something is built, and it makes a 3D model you can turn, take apart and
step through. Every part is named, and you can watch the detail go together.

It works from one 2D section or a picture of a detail, for any trade. Waterproofing is what we test it
on, because those details are among the hardest to read: thin layers, hidden laps, and parts a section
can't show. A tool that can follow them should manage most others.

Meta Global AI Developer Hackathon 2026 · Indie Developer Track · Build for Your World

## What you can do

- **Build from a drawing.** Drop in a PDF, PNG or JPG. Muse works out what the detail is, including the parts
  a section can't show, and builds the local assembly.
- **Explore it.** Isometric, front, side and top views, a scene tree of named parts with their sizes,
  pull apart, cut along the run, and a step-by-step playback of the build.
- **Change it.** Tell Muse what's wrong, and it edits the scene it already has. Undo steps, rename a scene, or delete it.
- **See what was checked.** A separate check compares the model with your drawing from five sides, and shows
  you what it found.
- **Take it with you.** Download any model as a GLB file.

## How it works

Muse doesn't generate a mesh. It writes Blender Python, one small step at a time, against a live scene:

```
Drawing ──> Muse reads it ──> writes a step ──> Blender runs it in a sandbox
                                   ^                       │
                                   └── renders and errors ─┘
                                             │
                              Muse asks to finish
                                             │
                         checker compares it with the drawing,
                         from five sides, and may send it back
                                             │
                        GLB export ──> three.js viewer in the browser
```

- **The model writes the 3D.** `builder/kit.py` holds generic shapes only (box, prism, sheet, lathe, rod).
  There are no ready-made components, so every real part is Muse's own code.
- **A scene is its list of code steps,** replayed in order. Undo is instant, feedback carries on from the
  same scene, and every size stays readable as a named value.
- **The checker is independent.** It lists what the drawing shows before any 3D exists, then ticks each
  item from renders. It never sees the builder's words, and code decides from the ticks. It stops after
  three refusals, and whatever is left is shown to you.
- **Model code is sandboxed.** It passes a static check, then runs in Blender on Modal with no network.
- **Viewing costs nothing.** Blender only runs during a build. The viewer is three.js on the exported
  GLB, in your browser.

## Quickstart

You need Python 3.13 or newer, Node 24, a [Meta Model API](https://dev.meta.ai/) key and a
[Modal](https://modal.com/) account.

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # add META_API_KEY

modal token new                 # once
modal deploy builder/sandbox.py

(cd web && npm install && npm run build)

python -m uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000 and drop in a drawing. A build takes about 5 to 15 minutes.

## Layout

```
app/                The server
  main.py           HTTP routes
  studio.py         Scenes: upload, build, feedback, undo
  worker.py         Runs build jobs, safe to restart
  repository.py     Jobs, leases and token budgets
  model_gateway.py  Budgeted, checkpointed model calls
  store.py          Finds the drawing in a PDF
builder/            The 3D
  loop.py           Muse's tools, and what it's told
  checker.py        The independent check
  kit.py            Generic shapes, materials and renders
  sandbox.py        Headless Blender on Modal, with no network
web/src/            The app, in React, TypeScript and three.js
migrations/         Database schema
tests/              Python tests, with the model and sandbox stubbed
```

## Development

```sh
python -m pytest tests/ -q            # no network needed
cd web && npx tsc -b && npm run lint  # type-check and lint
npm run dev                           # dev server, /api proxied to :8000
```

Scenes live in a SQLite database and files under `SETOUT_VAR` (default `var/`). Set `SETOUT_WORKER=external` and
run `python -m app.worker` to keep the worker apart from the server.

## Results so far

Built on `muse-spark-1.3-contributor`. Tokens are billed tokens, so cached input isn't counted.

| Detail | Parts | Tokens |
|---|---|---|
| Cavity wall base | 87 | 61,837 |
| Waterstop with mesh at joint | 53 | 64,377 |
| Roof parapet | 25 | 77,177 |
| Waterstop strips at wall joint | 29 | 77,397 |
| Roof kerb upstand | 16 | 95,104 |
| Floor drain on screed, with the checker | 47 | 214,593 |

On the floor drain, the checker raised the score from 6 to 12 of 14 items on the same list.

## Limits

- **Nothing here is verified.** Sizes are assumptions chosen by the model, because detail drawings aren't to
  scale, and every scene says so. Don't build from it without checking the source.
- **It can be wrong.** Muse can misread a drawing or add a part that isn't there, and the checker is a model too.
- **Models are simpler than hand-made ones.** They show how a detail goes together, not every fixing.
- **One build at a time.** A second upload waits in the queue.
- **It gives no compliance verdict.** It shows a detail, and doesn't grade real work.

## Licensing

Apache-2.0, see [LICENSE](LICENSE). Blender (`bpy`, GPL-3.0) runs as a separate service in the sandbox and
isn't distributed with this code.

Drawings, manuals and standards aren't ours to publish. `data/` and `*.pdf` are gitignored, and
[the rule is here](.claude/rules/source-documents.md).
