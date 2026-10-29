# Architecture (v0 vertical slice)

> **Historical.** This describes the first vertical slice. For the current
> system see `CLAUDE.md` (Playground architecture) and `README.md`; for the
> target architecture see `docs/PLAN_V3.md`.

```
                                    +------------------+
                                    | Conpro PDF (user |
                                    | upload, gitignored|
                                    +--------+---------+
                                             |
                        +--------------------v-------------------+
                        | FastAPI backend (app/main.py)          |
                        |  POST /api/documents -> sha256,        |
                        |    embedded figures (pypdf+pillow)     |
                        |  GET/PATCH /api/specs/{id}             |
                        |  POST /api/builds -> job {queued,      |
                        |    running, done, failed} (thread)     |
                        |  GET /api/builds/{id}/artifacts/*      |
                        +---+------------+------------+----------+
                            |            |            |
              +-------------v-+  +-------v------+  +--v--------------+
              | canonical     |  | mesh         |  | deterministic   |
              | spec v1       |  | generators   |  | verification    |
              | (app/spec.py, |  | closed set:  |  | (app/verify.py) |
              |  seed.py)     |  | box/l_angle/ |  | per-param tols, |
              | Pydantic, IDs |  | sheet/bead/  |  | contacts, report|
              | + cycle check |  | rod/fillet/  +--+ PASS/FAIL/UNV   |
              +-------------+  | wedge (mm)   |  +--^--------------+
                               +---+----+-----+     |
                                   |    |           |
                        scene.json |    | GLB  report.json
                        (viewer)   |    | (dl)  spec.json
                                   |    |
                        +----------v----v----------+
                        | viewer (web/: React+TS, |
                        | Vite; three r149 npm):  |
                        | crop+overlay, 3D, layer |
                        | toggle, evidence, checks|
                        +-------------------------+
```

Storage: SQLite + files under `var/` (SETOUT_VAR override, gitignored).
Units: mm in spec; viewer ×0.01, GLB metres (tested in tests/test_units.py).
Next to dock in: CadQuery STEP (rigid solids) + headless Blender
(sheets/render/GLB) behind the same spec; Modal behind the job record.
