# AGENTS.md

## Commands

- `scripts\regen.bat`: build the NCC viewer payloads
- `python -m pytest tests/ -q`: playground test suite (29 tests)
- `SETOUT_VAR=var python -m uvicorn app.main:app --port 8000`: playground server

## Code Map

- `app/`: playground backend (FastAPI, spec, generators, verification)
- `web/`: React + TS viewer (Vite; `npm run build` → served at /)
- `e2e/drive.py`: headless browser acceptance pass
- `tests/`: playground pytest suite
- `docs/`: GAP_ANALYSIS, PLAN, STATUS, EVAL, ARCH, DEMO
- `generator/`: NCC parametric details
- `viewer/`: NCC static viewers
- `perception/`: SAM kill-check
- `spec/`: original plan documentation
