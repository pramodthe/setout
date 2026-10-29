# AGENTS.md

## Commands

- `scripts/regen.sh` (or `scripts\regen.bat`): build NCC payloads
- `python -m pytest tests/ -q`: playground test suite (29 tests)
- `SETOUT_VAR=var python -m uvicorn app.main:app --port 8000`: playground server

## Code Map

- `app/`: playground backend (FastAPI, spec, generators, verification)
- `web/`: React + TS viewer (Vite; `npm run build` → served at /)
- `e2e/drive.py`: headless browser acceptance pass
- `tests/`: playground pytest suite
- `docs/`: GAP_ANALYSIS, PLAN, STATUS, EVAL, ARCH, DEMO
- `ncc/`: original NCC shower-details track
- `ncc/viewer/`: NCC static viewers
- `ncc/perception/`: SAM kill-check
