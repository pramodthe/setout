# Deploy setout-blender

Run this on your own machine, with your own Modal token. Nothing in this
repo deploys the app, and the app is not given a secret. Do not attach
`setout-llm` or any other Modal secret to `setout-blender`.

The function has `block_network=True` and no `secrets=`. It receives a
script and returns bytes. The web process keeps the API key.

## Commands

```sh
modal token set
modal deploy deploy/blender_app.py
```

`modal token set` asks for the token id and secret in the prompt. Do not
pass them as arguments, and do not put them in the environment of the
Blender app.

Leave the web process on local Blender until the deploy has finished.
Then point it at Modal:

```sh
BLENDER_TARGET=modal
```

Unset, empty, or any value other than `modal` stays on local Blender.
If `modal` is set but the app cannot be called, the job runs on local
Blender and the result warning says so.

## Smoke test

From the repo root, with the virtualenv active and `BLENDER_TARGET=modal`:

```sh
BLENDER_TARGET=modal python -c "from app.make.blender_backend import run; result = run('import bpy\nbpy.ops.mesh.primitive_cube_add(size=1)\n', {}, timeout=90, preview=False); print(result.get('backend'), result.get('ok'))"
```

A good deploy prints `modal True`. `local True` means the call fell back
to the Blender binary on this machine. The script has no API key in it.
