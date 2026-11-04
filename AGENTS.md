# Repository Guidelines

## Project Structure & Module Organization

- `app/`: FastAPI routes, scene orchestration, job workers, model gateway, and SQLite storage.
- `builder/`: Muse's build loop, independent checker, geometry measurements, generic shape kit, and Modal sandbox.
- `web/src/`: React, TypeScript, and three.js viewer; shared styles in `styles.css`.
- `migrations/`: Alembic schema revisions. Changes to `app/database.py` require a migration.
- `tests/`: Python regression tests and fixtures.
- `var/`: ignored runtime database and generated artifacts; `web/dist/`: generated frontend bundle.

## Build, Test, and Development Commands

Use Python 3.13+ and Node 24. Run from the repository root.

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                     # configure META_API_KEY
(cd web && npm ci && npm run build)       # install, type-check, bundle
python -m uvicorn app.main:app --port 8000 # API, built UI, embedded worker
(cd web && npm run dev)                  # Vite; proxies /api to :8000
python -m pytest tests/ -q               # offline regression suite
(cd web && npm test)                    # Node tests for frontend logic
(cd web && npx tsc -b && npm run lint)    # TypeScript and oxlint
```

Live builds require Modal authentication and `modal deploy builder/sandbox.py`; redeploy after sandbox changes. Kit changes travel with each call.
Optional finishing uses `SETOUT_RENDERER=local` with Blender, or `modal` after `modal deploy builder/render_sandbox.py`. It runs separately from builds and never executes generated Python locally.

## Coding Style & Naming Conventions

Use four-space indentation and `snake_case` functions/modules in Python. Use two-space indentation, double quotes, and semicolons in frontend source; React components/files use PascalCase, helpers use camelCase. Match surrounding code. Frontend linting uses oxlint; no Python linter or automatic formatter is configured. Kit dimensions use millimetres; GLB uses metres.

## Testing Guidelines

Use pytest with `test_*.py` files and descriptive `test_*` functions. Stub model and sandbox calls; `tests/conftest.py` isolates runtime storage. Run focused checks with `python -m pytest tests/test_studio.py -q -k undo`. Frontend logic tests use Node's test runner in `web/tests/*.test.ts`. Add regressions for changed behavior. CI runs both suites, TypeScript checks, lint, and build. No coverage threshold is configured; manually verify UI changes.

## Commit & Pull Request Guidelines

History uses descriptive, sentence-style subjects, often with merged PR numbers; Conventional Commit prefixes are not established. Describe the behavior changed. PRs should explain purpose, validation, relevant issues, and include screenshots for UI changes.

## Architecture & Data Rules

Keep `builder/kit.py` generic; never hand-model components. Preserve checker independence; reports and measurements stay in backend diagnostics (`/api/studio/{id}/diagnostics`), without changing verdicts. Generated code must pass static checks and run only in the network-disabled Modal sandbox. Sizes remain assumptions, marked “Not verified.” Never commit secrets, runtime state, or third-party source documents. Follow `CLAUDE.md` and `.claude/rules/source-documents.md` for details.
