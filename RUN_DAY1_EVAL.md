# Day 1 — Muse Spark bpy go/no-go

Run this from the repo root on your machine. The eval reads credentials from `.env` and never writes them into the report. Share back only:

`eval/make/out/day1_report.md`

## Commands

Local Blender 4.2 LTS:

```sh
python -m eval.make.muse_bpy_eval --target local
```

Modal worker (Blender runs on Modal; the LLM call still uses `.env` on this machine):

```sh
python -m eval.make.muse_bpy_eval --target modal
```

The command loads `.env`, creates `eval/make/out/` if needed, and writes the redacted Markdown table. `--target modal` deploys `setout-blender` when that app is not already deployed. The first deploy builds an image that downloads Blender 4.2.23. That app has no secrets and blocks network access at runtime.

Exit 0 means every profile that ran is GO. Exit 1 means a profile is NO-GO, or the eval stopped. Exit 2 means no profile was configured.

A prompt passes when the script runs, the scene has at least 5 named mesh objects, at least one collection, and `tree.json` is valid. A profile is GO at 3 or more passes out of 5.

## `.env` variable names

Copy `.env.example` to `.env` (gitignored). Set values only in `.env`:

- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `ALT_LLM_BASE_URL`
- `ALT_LLM_API_KEY`
- `ALT_LLM_MODEL`
- `BLENDER_BIN` (optional for `--target local`)

`LLM_MODEL` overrides the default profile only. When it is unset, that profile uses `muse-spark-1.3`. A profile with any required variable unset is SKIPPED, not scored. `BLENDER_BIN` is optional: the local target uses `blender` on `PATH`, then `/opt/blender/blender`.

Install deps once if you have not: `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`.

Blender 4.2 LTS pin for the local binary and the Modal image: 4.2.23, from `https://download.blender.org/release/Blender4.2/blender-4.2.23-linux-x64.tar.xz`.

## Modal, with your own token

`modal token` is already set up. Do not put a token in the repo or in `.env`.

Create the secret once. It is for the web/agent function later. It is not attached to `setout-blender`.

```sh
modal secret create setout-llm \
  LLM_BASE_URL=<base-url> \
  LLM_API_KEY=<api-key> \
  LLM_MODEL=<model-id> \
  ALT_LLM_BASE_URL=<alt-base-url> \
  ALT_LLM_API_KEY=<alt-api-key> \
  ALT_LLM_MODEL=<alt-model-id>
```

Replace each `<...>` locally. The day-1 command still reads the same names from `.env`, because the LLM call stays on this machine.

## What to send back

`eval/make/out/day1_report.md`

That file has the score table, per-prompt pass/fail, short error tails, and the profile and model names. It is passed through a redaction step before it is written.
