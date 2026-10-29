# Frontend migration scope: vanilla JS → React + TypeScript

Status: done. The viewer is React + TypeScript in `web/` (see `docs/STATUS.md`).

## Why migrate

The viewer (`app/static/app.js`, ~700 lines) now covers 13 interactions and
two details. Vanilla JS is holding, but the next features — SAM mask
editing, multi-detail comparison, selector rules UI — will push it past
comfortable size. React + TS (per brief §6) buys component boundaries,
typed API contracts, and testable UI logic.

## Stack decisions

- Vite + current stable React + TypeScript (strict), `three` via npm
  pinned (same 0.149 line initially, then current).
- Plain Three.js inside a `Viewport` component with refs — NOT React Three
  Fiber for v1. Reason: the render code (orbit, picking, clip plane,
  polygonOffset) is already correct and driven; port it verbatim first,
  adopt R3F patterns later if wanted.
- No UI framework: keep the existing dark CSS (move to CSS modules).
  No state library: `useReducer` + context is enough at this size.
- API client: typed `fetch` wrapper generated from the FastAPI OpenAPI
  schema (`/openapi.json`) — single source of truth, no hand drift.
- Tests: keep the Playwright drive (`/tmp/drive_viewer.py` → move into
  repo as `e2e/drive.py`) as the acceptance gate; add Vitest for
  provenance-badge and check-render logic.

## Phases (each shippable, old viewer kept until cutover)

1. Scaffold + shell (0.5 day): Vite app in `web/`, FastAPI serves
   `web/dist` in prod and proxies Vite dev in debug. Health: page loads,
   no console errors.
2. Port read-only panels (1 day): upload, crops, overlay, component list,
   evidence, verification, unresolved. Acceptance: byte-same API calls,
   drive steps 1–3 pass against the new UI.
3. Port 3D viewport (1 day): verbatim port of scene setup, meshes,
   picking, explode, clip, playback. Acceptance: drive steps 4–9 pass,
   screenshots match within reason.
4. Port mutations (0.5 day): param edit + rebuild, downloads.
   Acceptance: full drive green, 0 console errors.
5. Cutover + cleanup (0.5 day): delete `app/static/`, move drive script
   into repo, update README/ARCH. Acceptance: 29 pytest + full drive green.

Non-goals: visual redesign, R3F rewrite, PDF.js paging
(separate item), mobile layout rework.

## Risks

- Behaviour drift during port → mitigated by the existing drive script as
  an unchanged oracle (it must pass unmodified).
- three.js version bump surprises → pin first, bump later as its own step.
