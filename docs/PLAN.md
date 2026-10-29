# Plan — door-sill vertical slice, then layered completion

> **Historical.** This describes the first vertical slice. For the current
> system see `CLAUDE.md` (Playground architecture) and `README.md`; for the
> target architecture see `docs/PLAN_V3.md`.

Primary detail: Conpro p.1 drawing 3, "STANDARD SILL SECTION DOOR OPENING"
(D3). Why: a true 2D section (extrudable) with explicit source numbers —
6 mm gap, 100 mm minimum step, 1:80 min fall — plus 12+ labelled components.

## Architecture (new code in `app/`, old paths untouched)

```
PDF upload -> app/api (FastAPI) -> var/docs/{id}/original.pdf (sha256)
  -> embedded-figure extraction (pypdf+pillow, BSD/HPND) -> crops served
  -> seed spec (human transcription, labelled) -> corrections API
  -> canonical spec v1 (Pydantic) -> generators -> meshes + scene.json + GLB
  -> verify() -> report {PASS,FAIL,UNVERIFIED,N/A} -> viewer + EVAL.md
```

- Units: mm in spec; viewer scale 0.01 (metres). Conversion tested.
- Component IDs stable across spec, meshes, GLB, viewer, report.
- Generators are approved, parameter-driven, closed-set (`solid`, `sheet`,
  `bead`, `fillet`, `annotation`). The model never emits geometry code.
- Thin layers: physical thickness stored; display exaggeration stored
  separately and labelled in the viewer.

## Milestones (in delivery order)

1. Audit + plan — GAP/PLAN/STATUS. Done 28 Oct.
2. Vertical slice (MUST): upload → D3 crop → validated spec →
   generated assembly → viewer with layer toggle + click-evidence +
   verification report. Single-process FastAPI + SQLite, `var/` storage.
   Geometry v0 = dimension-driven mesh generators + hand-rolled GLB export
   (no new heavy deps); CadQuery/Blender dock into the same spec next.
3. Traceable interpretation (MUST): label overlay on crop, per-parameter
   provenance (drawing / manufacturer doc / user input / assumption),
   corrections API, unresolved list with build impact.
4. Accurate components (MUST→SHOULD): membrane/flashing follow section
   profile with upturns/terminations; sealant bead; triangular fillet;
   packers; fixings as annotated positions where the section under-specifies.
5. Viewer + sequence (SHOULD): exploded view, section view,
   install playback, one documented selector variation, `docs/EVAL.md`.
6. Evaluation + submission: EVAL harness stages A/B/C
   on D3 + 1–2 extra cases that genuinely work; README; demo script/video;
   arch diagram; MUSE attribution (paper v3 commit pinned when fetched).
7. After all above PASS: param edit + partial rebuild, Spark vs
   Wonder, downloads, Baseten/Fireworks.

## Out of scope (unchanged)

IFC/BIM, accounts, catalogues, native mobile, model training, self-hosted
LLM, pixel→mm measurement of install photos, compliance verdicts on real
work. SAM 3D Objects / image-to-3D never for verified geometry.

## Dependencies (pinned at build time in `requirements.txt`)

pypdf (BSD-3), pillow (HPND), fastapi + uvicorn + pydantic v2 (MIT/BSD),
pytest (MIT). Deliberately no PyMuPDF (AGPL) server-side. three.js pinned CDN
in viewer with console-error gate. cadquery + bpy stayed out of v0 (decision
recorded in STATUS); CadQuery now runs in the Modal sandbox.
