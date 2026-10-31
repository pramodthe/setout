# 3D Make demo

Local Blender. No Modal token. The model key stays in `.env` on this machine
and is never printed.

## Start

From the repo root, with the virtualenv active:

```sh
source .venv/bin/activate
SETOUT_VAR=var python -m app.make.seed_demo
SETOUT_VAR=var python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/#/make`. `BLENDER_TARGET` stays unset or `local`.

The seed command prints `demo-floor-waste`. That project is the hand-authored
floor-waste scene. It is not model output. Open it when a live model call is
too slow:

`http://127.0.0.1:8000/#/make/demo-floor-waste`

## Click path

1. `#/make` opens a new project. The model chip reads Muse Spark 1.3.
2. Attach a sketch (PNG, JPEG, or one PDF page). Type: `Bathroom floor waste junction: screed to falls, membrane with 100mm upturn at wall, stainless angle, tile and sealant.` Send.
3. The status line reads `Building, attempt 1 of 4`. A repair reads `Repairing, attempt n of 4`. The scene loads in the viewport. The version line shows the model name.
4. Play. Parts drop in and fade in `build_order`. Pause or drag the scrubber. The label names the arriving part.
5. Click Upturn in the scene tree. Set its height to 150. The viewport updates before Apply. Undo and redo are Ctrl+Z and Ctrl+Shift+Z. Apply rebuilds the next version.
6. In the composer: `add a puddle flange at the waste and lower tile 2mm`. Send. That is the next version. The previous version stays in the version menu and its `.glb` still downloads.
7. Quality Draft, then Use Blender. The PNG appears with Download render. Draft is the one that stays inside a minute on CPU. Download `scene.glb` from the bottom bar.
8. Settings: pick the alt profile (same base URL and key, saved on the server) and send a new prompt. The version line shows that profile's model. Keys are not on the page.

Keys while not typing in a field: O orbit, P pan, M move, F focus.

## Fallback

| What went wrong | Do this |
|---|---|
| The model call is still running, or it failed | Open `#/make/demo-floor-waste`. Play, select Upturn, and render from that scene. Say it is the reference script, not a live model run. |
| The status stream drops | The page shows Reconnecting… and resumes the same job. Reload the project if it stays failed. |
| Use Blender on High is still rendering | Switch Quality to Draft and render again. High is Cycles on CPU and is not the timed path. |
| Alt profile is not configured | Settings, alt profile, same base URL and model as Muse, paste the key, Save, Test connection. The key is not shown again. |

## Modal, on your machine

Not required for this demo. When you want it, follow `DEPLOY_MODAL.md`: `modal token set`, `modal deploy deploy/blender_app.py`, then `BLENDER_TARGET=modal`. The Blender app receives no secret. If the call cannot be made, the job uses local Blender.
