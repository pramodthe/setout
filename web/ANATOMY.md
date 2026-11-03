# Setout house story

The Setout landing page at `/#/` uses the supplied Blender assembly to show the
hidden complexity inside an ordinary room. The former `/#/anatomy` URL redirects
to the landing page. Visitors can enter the drawing-to-3D Studio at `/#/app`, then
open their own scenes at `/#/studio/<id>`. Start Vite with `npm run dev` from `web/`.

The story asks **What holds up your floor?** It opens on a close, above-ground
view into the room, with the window, door and timber floor in view. An opaque
studio ground surface conceals the basement in this opening; the source geometry
is retained for the later construction study. It moves from the room to
lifted subfloor panels, the timber floor framing, and the actual source-model
`Simpson_LUS210` hanger with its adjoining I-joist and LVL beam. It then pulls
back to an exploded home to explain why granular construction details matter,
introduces Setout's one-detail-at-a-time workflow, reassembles, and returns to
the opening view. The house model illustrates the problem; Setout Studio builds
local assemblies from uploaded detail drawings rather than generating a whole
house.
The close-up isolates and crops surrounding geometry for a readable detail; it
is an illustrative study, not a verified connection design or installation guide.
The general hanger description links to the manufacturer's product information.

The story uses a scroll-controlled sequence rendered in Blender Cycles. It has
studio lighting, soft contact shadows, physically based surface roughness, metal
reflections, and depth of field. The page plays the pale "fog" look: the floor
fades into the backdrop past the subject, and the page takes its colour from the
first frame's backdrop, so the film has no edge. Type is Inter Tight
(`@fontsource-variable/inter-tight`, OFL), black on that grey; the model is the
only colour on the page. In the opening, the mouse steers the camera across the
rendered orbit; it drifts on its own when the mouse rests, and scrolling carries
on from wherever it stopped. Camera motion, subfloor separation, and the
building explosion are rendered from the real geometry. Adjacent frames blend
for smooth scrubbing; different camera setups use short dissolves. The optional
CAD explorer loads three.js and the original interactive export only when opened.
It supports orbit, zoom, filters, an explosion slider, and detail views.
Reduced-motion mode (the system setting) stops the opening camera loop and the
headline rise, and uses chapter views.

## Rendered film

The render pipeline creates a separate project and never saves over the supplied
Blender file. From the repository root, after generating the model assets below:

```sh
# The page's pale look: preview five draft shots, then render and pack it.
/Applications/Blender.app/Contents/MacOS/Blender --background \
  var/blender-cleanup/3d-exploded.blend \
  --python scripts/render_anatomy.py -- --look fog --look-preview --sweep 3
/Applications/Blender.app/Contents/MacOS/Blender --background \
  var/blender-cleanup/3d-exploded.blend \
  --python scripts/render_anatomy.py -- --look fog --sequence --sweep 3
.venv/bin/python scripts/pack_anatomy_film.py --look fog

# The original charcoal look, kept in public/assets/anatomy/film/.
/Applications/Blender.app/Contents/MacOS/Blender --background \
  var/blender-cleanup/3d-exploded.blend \
  --python scripts/render_anatomy.py -- --preview
/Applications/Blender.app/Contents/MacOS/Blender --background \
  var/blender-cleanup/3d-exploded.blend \
  --python scripts/render_anatomy.py -- --sequence
.venv/bin/python scripts/pack_anatomy_film.py
```

`--sweep` widens every segment's camera orbit (1 keeps the original moves; the
page uses 3). Each look renders to its own `frames*/` folder and `sequence*.json`,
and packs to its own `film*/` folder, so neither overwrites the other. The player
reads `film-fog/`. The Python encoder requires Pillow. Rendering uses Metal on this Mac; `--cpu`
selects CPU rendering. The first GPU render can take longer while compiling
shaders. Use `--hero-preview` to compare the exterior, side and interior cameras
before rendering the sequence. Outputs are 116 frames encoded for the web at 1280 × 800, an editable, packed Blender
project configured for 1600 × 1000 with timeline keys and chapter markers, and an animated WebP preview in
`var/anatomy-cinema/`. The encoder writes WebP frames and a versioned manifest to
`public/assets/anatomy/film/`. The player fetches nearby frames on demand, keeps
a bounded decoded-frame cache, and reduces decode size on mobile.

The portable WebP preview appends a reverse explosion so it ends assembled.

The render-only copy removes reverse polygon sheets by part and plane, including
opposite faces with different triangulations. Those imported sheets caused
zero-distance self-shadowing in Cycles. On the roof, exterior shingle/asphalt
materials take precedence over timber-textured reverse faces. Roof sheets with
matching winding but alternate triangulation are also resolved by exterior
material, and the planar shingles use geometric normals in place of incorrect
imported split normals. Selected section cuts are closed, and the
detail receives a small bevel for edge highlights. The original source geometry
and original explosion keys remain in the supplied file. Before intentionally
rerendering changed materials or cameras, move the affected frames aside:
sequence rendering resumes existing frames. The opening is frames 000–024;
the whole-building explosion is 083–115.

## Generated model assets

`public/assets/anatomy/` is intentionally ignored by Git. It contains `house.json`,
`house.bin.gz`, `textures/`, `film/` and `film-fog/`. These must exist before `npm run build`; Vite
copies them to `dist/assets/anatomy/`, served by the existing FastAPI asset mount.

To regenerate, open the supplied exploded Blender file, execute
`scripts/export_anatomy.py` in Blender, and call `export_anatomy()` with the absolute
path to `web/public/assets/anatomy/`. The export temporarily evaluates frame 1 and
restores the previous frame. It does not change source meshes, materials or keys.

Version 2 preserves per-face material assignments, source diffuse textures, UVs,
and hard normals. Source images are deduplicated by content. The inspected LVL
and selected joist web have extremely stretched imported UVs; their presentation
UVs are reprojected in the web export. This does not edit the Blender file.

The export removes same-facing coincident triangles, prioritizing textured faces,
and corrects winding for mirrored transforms. Opposite-facing sheet surfaces
remain and use front-face rendering. Vertex positions are not rounded. Sibling
surfaces and the selected connection's subparts stay together during separation.
The renderer uses environment lighting without shadow-map acne or ghost overlays,
and adjusts the camera near plane to each shot for depth precision.

The current asset has 6,405 source mesh parts, 1,438,026 triangles, 116 material
batches, and 52 unique texture files from 76 referenced source images. Export-only
cleanup removed 9,150 coincident/degenerate triangles. Three imported drawing
backing/frame objects are excluded. Mesh-part count is not a bill of materials.
System categories are inferred for visual exploration and are not verified.

## Validation

Use Node 24: `npm run build` and `npm run lint`. Check the opening, forward/reverse
scrolling, floor lift, connection shot, full explosion, reassembly, explorer
filters, both close-ups, slider endpoints, Escape, mobile layout, and reduced
motion. Validate binary attribute lengths, finite coordinates, index bounds,
material-group coverage, and texture availability after an export.

Loading has a retry state. The written story remains available if image assets
fail, and the rendered story works without WebGL. The optional explorer's geometry
is approximately 14 MB compressed; textures load locally with the model. The
supplied opening is a construction cutaway, not a furnished interior render.
