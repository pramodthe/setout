"""Build and render a photographic presentation of the supplied house geometry.

Run in background Blender with the source .blend open. Writes a separate studio
project and rendered assets; does not save over the source Blender file.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
import time
from pathlib import Path

import bpy
import bmesh
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web/public/assets/anatomy"
OUT = ROOT / "var/anatomy-cinema"
# "studio" is the charcoal photographic set; "fog" is a pale cyclorama whose
# horizon dissolves into the page. Both light the same source geometry.
LOOKS = {
    "studio": {"world": (0.13, 0.16, 0.19), "strength": 0.22, "backdrop": (.001, .0015, .0012), "stage": (.006, .007, .0065), "exposure": 0.35, "suffix": ""},
    "fog": {"world": (0.62, 0.63, 0.64), "strength": 0.55, "backdrop": (.78, .785, .79), "stage": (.36, .365, .37), "exposure": 0.0, "suffix": "-fog", "fog": (1.1, 2.6)},
}
LOOK = LOOKS["studio"]


def studio_material(source, name, index):
    mat = bpy.data.materials.new(f"Studio / {name or 'surface'} / {index}")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*source["color"], 1)
    bsdf.inputs["Roughness"].default_value = 0.65
    bsdf.inputs["Metallic"].default_value = source["metalness"]
    tex = None
    if source["texture"]:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(ASSETS / source["texture"]), check_existing=True)
        tex.interpolation = "Linear"
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    low = name.lower()
    coord = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 180
    noise.inputs["Detail"].default_value = 3
    links.new(coord.outputs["Object"], noise.inputs["Vector"])
    rough = nodes.new("ShaderNodeMapRange")
    rough.inputs["From Min"].default_value = 0
    rough.inputs["From Max"].default_value = 1
    rough.inputs["To Min"].default_value = 0.5
    rough.inputs["To Max"].default_value = 0.8
    links.new(noise.outputs["Fac"], rough.inputs["Value"])
    links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.16
    bump.inputs["Distance"].default_value = 0.0006
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    bevel = nodes.new("ShaderNodeBevel")
    bevel.samples = 3
    bevel.inputs["Radius"].default_value = 0.0008
    # Source sheets contain coincident reverse faces; bevel ray sampling
    # across those faces creates black normals. Keep the geometric normal.
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    if any(word in low for word in ["wood", "stud", "grain", "osb", "advantech", "laminate"]):
        bsdf.inputs["Metallic"].default_value = 0
        rough.inputs["To Min"].default_value = 0.48
        rough.inputs["To Max"].default_value = 0.72
        if tex:
            links.new(tex.outputs["Color"], bump.inputs["Height"])
        bump.inputs["Distance"].default_value = 0.0007
        if "plywood" in low:
            hue = nodes.new("ShaderNodeHueSaturation")
            hue.inputs["Saturation"].default_value = 0.42
            hue.inputs["Value"].default_value = 0.78
            links.new(tex.outputs["Color"], hue.inputs["Color"])
            links.new(hue.outputs["Color"], bsdf.inputs["Base Color"])
    elif any(word in low for word in ["metal", "galvan", "alumin"]):
        bsdf.inputs["Metallic"].default_value = 0.94
        rough.inputs["To Min"].default_value = 0.25
        rough.inputs["To Max"].default_value = 0.39
        bump.inputs["Distance"].default_value = 0.00008
        if tex:
            links.new(tex.outputs["Color"], rough.inputs["Value"])
    elif any(word in low for word in ["concrete", "stucco", "asphalt"]):
        bump.inputs["Strength"].default_value = 0.4
        bump.inputs["Distance"].default_value = 0.003
        rough.inputs["To Min"].default_value = 0.78
        rough.inputs["To Max"].default_value = 0.94
        bevel.inputs["Radius"].default_value = 0.002
    elif not tex:
        bevel.inputs["Radius"].default_value = 0.0015
        bump.inputs["Distance"].default_value = 0.00015
    return mat


def make_mesh(name, pos, uv, triangles, material_ids, materials, normals):
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(pos))
    mesh.vertices.foreach_set("co", pos.ravel())
    mesh.loops.add(triangles.size)
    mesh.loops.foreach_set("vertex_index", triangles.ravel())
    mesh.polygons.add(len(triangles))
    mesh.polygons.foreach_set("loop_start", np.arange(len(triangles), dtype=np.int32) * 3)
    mesh.polygons.foreach_set("loop_total", np.full(len(triangles), 3, dtype=np.int32))
    mesh.polygons.foreach_set("material_index", material_ids)
    mesh.polygons.foreach_set("use_smooth", np.ones(len(triangles), dtype=bool))
    for mat in materials:
        mesh.materials.append(mat)
    mesh.uv_layers.new(name="Source UV")
    mesh.uv_layers.active.data.foreach_set("uv", uv[triangles.ravel()].ravel())
    mesh.update()
    mesh.normals_split_custom_set_from_vertices(normals.tolist())
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def cut_detail(obj, center):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    # Weld export-only vertex splits so each cut can be closed independently.
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=0.000001)
    # Section the existing members to reveal the connection. No new building parts.
    for axis, low, high in [(0, center[0] - .53, center[0] + .53), (1, center[1] - .20, center[1] + .63)]:
        for bound, direction in [(low, -1), (high, 1)]:
            co = [0.0, 0.0, 0.0]; co[axis] = bound
            normal = [0.0, 0.0, 0.0]; normal[axis] = direction
            result = bmesh.ops.bisect_plane(bm, geom=list(bm.verts)+list(bm.edges)+list(bm.faces),
                plane_co=co, plane_no=normal, clear_outer=True, clear_inner=False, dist=0.000001)
            boundary = [e for e in result['geom_cut'] if isinstance(e, bmesh.types.BMEdge) and e.is_boundary]
            if boundary:
                caps = bmesh.ops.holes_fill(bm, edges=boundary, sides=0)['faces']
                uv_layer = bm.loops.layers.uv.active
                for face in caps:
                    neighbours = [f.material_index for e in face.edges for f in e.link_faces if f!=face]
                    if neighbours: face.material_index = Counter(neighbours).most_common(1)[0][0]
                    face.smooth = False
                    for loop in face.loops:
                        if uv_layer:
                            co = loop.vert.co
                            loop[uv_layer].uv = (co[1]/.6,co[2]/.6) if axis==0 else (co[0]/.6,co[2]/.6)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    obj.data.polygons.foreach_set('use_smooth',np.zeros(len(obj.data.polygons),dtype=bool))
    obj.data.normals_split_custom_set_from_vertices([(0,0,0)]*len(obj.data.vertices))
    bevel = obj.modifiers.new("Subtle photographed edge highlights", 'BEVEL')
    bevel.width = 0.0008
    bevel.segments = 3
    bevel.limit_method = 'ANGLE'


def area(name, location, target, energy, size, color):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.shape, data.size, data.color = energy, "DISK", size, color
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat('-Z', 'Y').to_euler()
    return obj


def prepare(cpu=False):
    image_names = {}
    for image in bpy.data.images:
        if image.packed_file:
            image_names[hashlib.sha256(bytes(image.packed_file.data)).hexdigest()[:16]] = image.name
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = 'METAL'
    prefs.refresh_devices()
    for device in prefs.devices:
        device.use = device.type == 'METAL'
    scene.cycles.device = 'CPU' if cpu else 'GPU'
    scene.cycles.samples = 48
    scene.cycles.use_denoising = True
    scene.cycles.adaptive_threshold = 0.035
    scene.cycles.max_bounces = 6
    scene.cycles.diffuse_bounces = 3
    scene.cycles.glossy_bounces = 4
    scene.render.use_persistent_data = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.resolution_x, scene.render.resolution_y = 1600, 1000
    scene.render.resolution_percentage = 100
    scene.render.fps = 24
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = LOOK["exposure"]
    world = bpy.data.worlds.new("Soft studio ambience")
    world.use_nodes = True
    world.node_tree.nodes['Background'].inputs['Color'].default_value = (*LOOK["world"], 1)
    world.node_tree.nodes['Background'].inputs['Strength'].default_value = LOOK["strength"]
    # Keep the illumination independent of the photographed background.
    path = world.node_tree.nodes.new('ShaderNodeLightPath')
    camera_bg = world.node_tree.nodes.new('ShaderNodeBackground')
    camera_bg.inputs['Color'].default_value = (*LOOK["backdrop"], 1)
    mix = world.node_tree.nodes.new('ShaderNodeMixShader')
    world.node_tree.links.new(path.outputs['Is Camera Ray'],mix.inputs[0])
    world.node_tree.links.new(world.node_tree.nodes['Background'].outputs[0],mix.inputs[1])
    world.node_tree.links.new(camera_bg.outputs[0],mix.inputs[2])
    world.node_tree.links.new(mix.outputs[0],world.node_tree.nodes['World Output'].inputs['Surface'])
    scene.world = world
    meta = json.loads((ASSETS / 'house.json').read_text())
    buffer = gzip.decompress((ASSETS / 'house.bin.gz').read_bytes())
    dtype = {'f32':'<f4','u32':'<u4','i8':'i1','u8':'u1'}
    arrays = {name: np.frombuffer(buffer, dtype=dtype[a['type']], count=a['length'], offset=a['offset']).reshape(-1,a['itemSize']) for name,a in meta['attributes'].items()}
    # Web Y-up -> Blender Z-up, same source dimensions and relative coordinates.
    convert = np.array([[1,0,0],[0,0,-1],[0,1,0]],dtype=np.float32)
    pos = arrays['position'] @ convert.T
    offsets = arrays['explode'] @ convert.T
    normals = (arrays['normal'].astype(np.float32) / 127) @ convert.T
    triangles = arrays['index'].reshape(-1,3)
    uv = arrays['uv']
    feature = arrays['feature'].ravel()
    material_ids = np.zeros(len(triangles), dtype=np.int32)
    for group in meta['groups']:
        material_ids[group['start']//3:(group['start']+group['count'])//3] = group['material']
    material_names = [image_names.get(Path(mat['texture']).stem, '').lower() if mat['texture'] else '' for mat in meta['materials']]
    materials = [studio_material(mat, material_names[i], i) for i, mat in enumerate(meta['materials'])]
    # The source shingles have a timber-textured reverse side. Choosing the
    # first textured face selects that back instead of the asphalt exterior.
    # Prefer the finish before resolving coincident, differently triangulated
    # planes; keep the supplied UVs and actual shingle geometry.
    surface_priority = np.array([
        0 if ('ac136' in name or 'asphalt' in name) else
        4 if 'wood_translucent' in name else
        2 if mat['texture'] else 3
        for mat, name in zip(meta['materials'], material_names)
    ])
    # SketchUp sheets also contain reverse polygons with DIFFERENT diagonals.
    # Choose one facing direction per source part/plane before triangle deduping.
    # Cycles materials already shade both sides, so reverse sheets only create
    # zero-distance self-shadowing. The source project stays untouched.
    part_ids = np.zeros(len(pos), dtype=np.int32)
    for index, part in enumerate(meta['parts']):
        part_ids[part['start']:part['start']+part['count']] = index
    roof_faces = surface_priority[material_ids] == 0
    roof_parts = np.unique(part_ids[triangles[roof_faces, 0]])
    roof_vertices = np.isin(part_ids, roof_parts)
    # Imported split normals point through some shingle faces. These planar
    # sheets should use their geometric normals, without interpolated creases.
    normals[roof_vertices] = 0
    corners = pos[triangles]
    face_normals = np.cross(corners[:,1]-corners[:,0], corners[:,2]-corners[:,0])
    face_normals /= np.maximum(np.linalg.norm(face_normals,axis=1,keepdims=True),1e-12)
    dominant = np.argmax(np.abs(face_normals),axis=1)
    facing = np.where(face_normals[np.arange(len(triangles)),dominant]>=0,1,-1)
    planes = face_normals * facing[:,None]
    keys = np.column_stack((part_ids[triangles[:,0]],np.round(planes*10000),np.round(np.sum(planes*corners[:,0],axis=1)*100000))).astype(np.int64)
    priority = np.argsort(surface_priority[material_ids], kind='stable')
    _,first,inverse = np.unique(keys[priority],axis=0,return_index=True,return_inverse=True)
    chosen = facing[priority[first]][inverse]
    chosen_material = material_ids[priority[first]][inverse]
    roof_surface = roof_vertices[triangles[priority, 0]] & (surface_priority[chosen_material] == 0)
    # Some duplicate roof faces have the SAME winding but alternate diagonals.
    # Facing alone cannot resolve these: retain the exterior material's sheet.
    keep = np.sort(priority[(facing[priority] == chosen) &
        (~roof_surface | (material_ids[priority] == chosen_material))])
    print(f"Removed {len(triangles)-len(keep)} reverse plane faces",flush=True)
    triangles,material_ids = triangles[keep],material_ids[keep]
    _, point_ids = np.unique(np.round(pos, 6), axis=0, return_inverse=True)
    canonical = np.sort(point_ids[triangles], axis=1)
    priority = np.argsort(surface_priority[material_ids], kind='stable')
    _, unique = np.unique(canonical[priority], axis=0, return_index=True)
    keep = np.sort(priority[unique])
    print(f"Removed {len(triangles)-len(keep)} reverse surface duplicates", flush=True)
    triangles, material_ids = triangles[keep], material_ids[keep]
    whole = make_mesh("HOME / source assembly", pos, uv, triangles, material_ids, materials, normals)
    whole.data.polygons.foreach_set('use_smooth', ~roof_vertices[triangles[:, 0]])
    whole.shape_key_add(name="Assembled")
    exploded = whole.shape_key_add(name="Every component / separated")
    exploded.data.foreach_set('co', (pos+offsets).ravel())
    assemblies = {'home': whole}
    for name, mask in [('floor',feature[triangles[:,0]]>0), ('detail',(feature[triangles[:,0]]>0)&(feature[triangles[:,0]]<4))]:
        faces = triangles[mask]
        used, inverse = np.unique(faces.ravel(), return_inverse=True)
        obj = make_mesh(name.upper()+" / source study",pos[used],uv[used],inverse.reshape(-1,3).astype(np.uint32),material_ids[mask],materials,normals[used])
        if name=='floor':
            obj.shape_key_add(name='Assembled')
            lift = obj.shape_key_add(name='Lift subfloor')
            lifted=pos[used].copy(); lifted[feature[used]==4,2]+=1.1
            lift.data.foreach_set('co',lifted.ravel())
        else:
            cut_detail(obj,(-3.285,-.45,-.832))
        assemblies[name]=obj
    camera_data=bpy.data.cameras.new("Architectural camera")
    camera=bpy.data.objects.new("Architectural camera",camera_data)
    bpy.context.collection.objects.link(camera)
    scene.camera=camera
    camera.data.lens=55
    camera.data.clip_start=.02
    camera.data.clip_end=400
    focus=bpy.data.objects.new("Camera focus",None)
    bpy.context.collection.objects.link(focus)
    camera.data.dof.use_dof=True
    camera.data.dof.focus_object=focus
    camera.data.dof.aperture_fstop=8
    bpy.ops.mesh.primitive_plane_add(size=4000, location=(0,0,-4.68))
    stage=bpy.context.object;stage.name="Studio cyclorama / shadow surface"
    stage_mat=bpy.data.materials.new("Charcoal photographic backdrop")
    stage_mat.use_nodes=True
    bsdf=stage_mat.node_tree.nodes['Principled BSDF']
    bsdf.inputs['Base Color'].default_value=(*LOOK["stage"],1)
    bsdf.inputs['Roughness'].default_value=.72
    if "fog" in LOOK:
        # Past the subject the floor fades to the backdrop, so the horizon
        # dissolves instead of falling off into an unlit band. Camera rays only:
        # the fade never lights the model.
        tree=stage_mat.node_tree
        view=tree.nodes.new('ShaderNodeCameraData')
        fade=tree.nodes.new('ShaderNodeMapRange');fade.name='Fog range'
        ray=tree.nodes.new('ShaderNodeLightPath')
        only=tree.nodes.new('ShaderNodeMath');only.operation='MULTIPLY'
        glow=tree.nodes.new('ShaderNodeEmission')
        glow.inputs['Color'].default_value=(*LOOK["backdrop"],1)
        mix=tree.nodes.new('ShaderNodeMixShader')
        tree.links.new(view.outputs['View Distance'],fade.inputs['Value'])
        tree.links.new(fade.outputs['Result'],only.inputs[0])
        tree.links.new(ray.outputs['Is Camera Ray'],only.inputs[1])
        tree.links.new(only.outputs[0],mix.inputs[0])
        tree.links.new(bsdf.outputs[0],mix.inputs[1])
        tree.links.new(glow.outputs[0],mix.inputs[2])
        tree.links.new(mix.outputs[0],tree.nodes['Material Output'].inputs['Surface'])
    stage.data.materials.append(stage_mat)
    lights=[
        area("Large warm key",(-8,-9,14),(0,0,0),10000,9,(1,.91,.78)),
        area("Cool window fill",(9,-3,7),(0,0,0),3600,7,(.78,.88,1)),
        area("Rim softbox",(2,8,13),(0,0,0),14000,7,(1,.94,.85)),
    ]
    scene['anatomy_source']='Source Blender assembly via cleaned v2 export; no invented construction components.'
    return scene,assemblies,stage,lights,focus


def shot(state, name, amount=0, orbit=0, draft=False):
    scene,assemblies,stage,lights,focus=state
    visible_name = 'home' if name == 'hero' else name
    for key, obj in assemblies.items():
        hidden = key != visible_name
        if obj.hide_render != hidden:
            obj.hide_render = hidden
    obj=assemblies[visible_name]
    if obj.data.shape_keys:
        value = amount if name in ('home', 'floor') else 0
        shape = obj.data.shape_keys.key_blocks[1]
        if shape.value != value:
            shape.value = value
    if name=='hero':
        center=Vector((-1.866,.827,2.007))
        yaw,pitch,distance=2.27+orbit,.20,22
        # An opaque ground surface hides the underground source geometry in
        # the opening, while retaining it for the later construction study.
        stage.location.z=-.635
        scale=.67
    elif name=='home':
        center=Vector((0,0,amount*.1))
        yaw,pitch,distance=.72+orbit,.22,35+amount*40
        stage.location.z=-4.68-amount*4.45
        scale=1+amount
    elif name=='floor':
        center=Vector((-1.4,.55,-.6+amount*.35))
        yaw,pitch,distance=2.35+orbit,.40,15
        stage.location.z=-1.3
        scale=.45
    else:
        center=Vector((-3.285,-.45,-.832))
        yaw,pitch,distance=2.4+orbit,.34,3.3
        stage.location.z=-1.015
        scale=.065
    direction=Vector((math.sin(yaw),-math.cos(yaw),math.sin(pitch))).normalized()
    right=Vector((math.cos(yaw),math.sin(yaw),0))
    target=center-right*(distance*.10)
    camera=scene.camera
    camera.location=target+direction*distance
    camera.rotation_euler=(target-camera.location).to_track_quat('-Z','Y').to_euler()
    focus.location=center
    fade=stage.active_material.node_tree.nodes.get('Fog range')
    if fade:
        near,far=LOOK["fog"]
        fade.inputs['From Min'].default_value=distance*near
        fade.inputs['From Max'].default_value=distance*far
    camera.data.dof.aperture_fstop=6.3 if name=='detail' else 9
    base=[((-8,-9,14),10000,9),((9,-3,7),3600,7),((2,8,13),14000,7)]
    for lamp,(loc,energy,size) in zip(lights,base):
        lamp.location=center+Vector(loc)*scale
        lamp.rotation_euler=(center-lamp.location).to_track_quat('-Z','Y').to_euler()
        lamp.data.energy=energy*scale*scale*(.5 if name=='hero' else 1)
        lamp.data.size=size*scale
    scene.render.resolution_percentage=50 if draft else 100
    scene.cycles.samples=24 if draft else 48
    bpy.context.view_layer.update()


def render(scene,path):
    scene.render.filepath=str(path)
    t=time.time()
    print(f"ANATOMY_START {path.name}",flush=True)
    result = bpy.ops.render.render(write_still=True)
    if 'FINISHED' not in result:
        raise RuntimeError(f"Render interrupted: {path.name}")
    print(f"ANATOMY_RENDER {path.name} {time.time()-t:.1f}s",flush=True)


def save_animated_project(state, schedule):
    scene, assemblies, stage, lights, focus = state
    bpy.context.preferences.edit.keyframe_new_interpolation_type = 'LINEAR'
    scene.frame_start = 1
    scene.frame_end = (len(schedule)-1)*2+1
    for index, (name, amount, orbit) in enumerate(schedule):
        frame = index*2+1
        scene.frame_set(frame)
        shot(state, name, amount, orbit)
        for obj in [scene.camera, stage, focus, *lights]:
            obj.keyframe_insert('location', frame=frame)
            obj.keyframe_insert('rotation_euler', frame=frame)
        scene.camera.data.dof.keyframe_insert('aperture_fstop', frame=frame)
        for light in lights:
            light.data.keyframe_insert('energy', frame=frame)
            light.data.keyframe_insert('size', frame=frame)
        for key, obj in assemblies.items():
            obj.hide_viewport = key != ('home' if name == 'hero' else name)
            obj.keyframe_insert('hide_render', frame=frame)
            obj.keyframe_insert('hide_viewport', frame=frame)
            if obj.data.shape_keys:
                obj.data.shape_keys.key_blocks[1].keyframe_insert('value', frame=frame)
    for frame, name in [(1, 'HOME'), (51, 'LIFT THE FLOOR'), (117, 'THE CONNECTION'), (167, 'EVERY COMPONENT')]:
        scene.timeline_markers.new(name, frame=frame)
    scene.frame_set(1)
    configure_workspace(scene)
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT/f'anatomy-photographic{LOOK["suffix"]}.blend'),compress=True)


def configure_workspace(scene):
    scene.cycles.preview_samples = 16
    scene.cycles.use_preview_denoising = True
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                space.region_3d.view_perspective = 'CAMERA'
                space.shading.type = 'RENDERED'
                space.overlay.show_overlays = False


def main():
    import sys
    parser=argparse.ArgumentParser()
    parser.add_argument('--preview',action='store_true')
    parser.add_argument('--sequence',action='store_true')
    parser.add_argument('--cpu',action='store_true')
    parser.add_argument('--detail-preview',action='store_true')
    parser.add_argument('--check-explosion',action='store_true')
    parser.add_argument('--hero-preview',action='store_true')
    parser.add_argument('--look',choices=LOOKS,default='studio')
    parser.add_argument('--look-preview',action='store_true')
    # Widens each segment's camera orbit; 1 keeps the original moves.
    parser.add_argument('--sweep',type=float,default=1.0)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    global LOOK
    LOOK=LOOKS[args.look]
    suffix=LOOK["suffix"]
    OUT.mkdir(parents=True,exist_ok=True)
    state=prepare(args.cpu)
    if args.look_preview:
        s=args.sweep
        for name,amount,orbit in [('hero',0,0),('hero',0,.08*s),('floor',1,.08*s),('detail',0,.12*s),('home',1,.2*s)]:
            shot(state,name,amount,orbit,draft=True)
            render(state[0],OUT/f'preview{suffix}-{name}-{amount}-{orbit:.2f}.png')
    if args.hero_preview:
        shot(state, 'hero')
        bpy.ops.file.pack_all()
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'hero-studio-preview.blend'), compress=True)
        for label, orbit in [('front', -1.55), ('side', -1.15), ('interior', 0)]:
            shot(state, 'hero', orbit=orbit, draft=True)
            render(state[0], OUT/f'preview-hero-{label}.png')
    if args.check_explosion:
        shot(state,'home',1,.2,draft=True)
        render(state[0],OUT/'preview-exploded.png')
    if args.detail_preview:
        shot(state,'detail',0,draft=True)
        render(state[0],OUT/'preview-detail-v7.png')
    if args.preview:
        for name,amount in [('home',0),('detail',0),('floor',1)]:
            shot(state,name,amount,draft=True)
            render(state[0],OUT/f'preview-{name}.png')
        shot(state,'home',0)
        bpy.ops.file.pack_all()
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'anatomy-photographic.blend'),compress=True)
    if args.sequence:
        frames=OUT/f'frames{suffix}';frames.mkdir(exist_ok=True)
        # Chapter anchor frames share the same lighting and source geometry.
        s=args.sweep
        schedule=[]
        for i in range(25):schedule.append(('hero',0,i/24*.08*s))
        for i in range(33):schedule.append(('floor',i/32,(-.08+i/32*.16)*s))
        for i in range(25):schedule.append(('detail',0,(-.12+i/24*.24)*s))
        for i in range(33):schedule.append(('home',i/32,(.08+i/32*.12)*s))
        for i,(name,amount,orbit) in enumerate(schedule):
            shot(state,name,amount,orbit)
            # Web motion is viewed at a smaller size than the editable master.
            state[0].render.resolution_x, state[0].render.resolution_y = 1280, 800
            state[0].cycles.samples = 24
            if not (frames/f'{i:03d}.png').exists():
                render(state[0],frames/f'{i:03d}.png')
        (OUT/f'sequence{suffix}.json').write_text(json.dumps({'frames':len(schedule),'chapters':[0,25,49,58,83,115,0],'width':1280,'height':800,'look':args.look}))
        state[0].render.resolution_x, state[0].render.resolution_y = 1600, 1000
        save_animated_project(state, schedule)

if __name__=='__main__':main()
