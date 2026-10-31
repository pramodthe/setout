import { useEffect, useRef, useState } from "react";
import type { JSX, ReactNode } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { TransformControls } from "three/examples/jsm/controls/TransformControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { axisScaleFactors, yUpMetresToZUpMm, type OverrideMap } from "./edits";
import type { PartPose } from "./playback";
import { needsZUpMmConversion, zUpMmToYUpMetres } from "./units";

export type GizmoMode = "translate" | "rotate";

function maxAbs(box: THREE.Box3): number {
  return Math.max(
    Math.abs(box.min.x), Math.abs(box.min.y), Math.abs(box.min.z),
    Math.abs(box.max.x), Math.abs(box.max.y), Math.abs(box.max.z),
  );
}

/** A millimetre Z-up scene becomes Y-up metres. A glTF already in metres is left as exported. */
export function orientLoadedModel(root: THREE.Object3D): void {
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  if (!needsZUpMmConversion(maxAbs(box))) return;
  root.scale.multiplyScalar(0.001);
  root.rotation.x -= Math.PI / 2;
  root.updateMatrixWorld(true);
}

function fit(camera: THREE.PerspectiveCamera, controls: OrbitControls, root: THREE.Object3D): void {
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.x, size.y, size.z) * 0.5 || 0.2;
  const distance = radius / Math.tan((camera.fov * Math.PI) / 360);
  const direction = new THREE.Vector3(1, 0.65, 1).normalize();
  camera.position.copy(center).add(direction.multiplyScalar(distance * 1.6));
  camera.near = Math.max(distance / 200, 0.001);
  camera.far = distance * 20;
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

const TO_Y_UP = new THREE.Matrix4().set(
  1, 0, 0, 0,
  0, 0, 1, 0,
  0, -1, 0, 0,
  0, 0, 0, 1,
);

function quaternionFromZUpEuler(deg: [number, number, number]): THREE.Quaternion {
  const euler = new THREE.Euler(
    THREE.MathUtils.degToRad(deg[0]),
    THREE.MathUtils.degToRad(deg[1]),
    THREE.MathUtils.degToRad(deg[2]),
    "XYZ",
  );
  const blender = new THREE.Matrix4().makeRotationFromEuler(euler);
  const gltf = new THREE.Matrix4().multiplyMatrices(TO_Y_UP, blender).multiply(TO_Y_UP.clone().invert());
  return new THREE.Quaternion().setFromRotationMatrix(gltf);
}

function zUpEulerFromQuaternion(q: THREE.Quaternion): [number, number, number] {
  const gltf = new THREE.Matrix4().makeRotationFromQuaternion(q);
  const inverse = TO_Y_UP.clone().invert();
  const blender = new THREE.Matrix4().multiplyMatrices(inverse, gltf).multiply(TO_Y_UP);
  const euler = new THREE.Euler().setFromRotationMatrix(blender, "XYZ");
  const deg = (rad: number) => Math.round(THREE.MathUtils.radToDeg(rad) * 1000) / 1000;
  return [deg(euler.x), deg(euler.y), deg(euler.z)];
}

interface Original {
  position: THREE.Vector3;
  quaternion: THREE.Quaternion;
  scale: THREE.Vector3;
}

function paintOpacity(object: THREE.Object3D, opacity: number): void {
  const mesh = object as THREE.Mesh;
  const list = mesh.material ? (Array.isArray(mesh.material) ? mesh.material : [mesh.material]) : [];
  for (const material of list) {
    material.transparent = opacity < 0.999;
    material.opacity = opacity;
    material.depthWrite = opacity >= 0.999;
    material.needsUpdate = true;
  }
  object.visible = opacity > 0.02;
}

export default function Viewport({ url, selected, edits, bases, playback, mode, onSelect, onCommit, children }: {
  url: string | null;
  selected: string | null;
  edits: OverrideMap;
  bases: Record<string, Record<string, number>>;
  playback: Record<string, PartPose> | null;
  mode: GizmoMode;
  onSelect: (name: string | null) => void;
  onCommit: (name: string, location: [number, number, number], rotation: [number, number, number]) => void;
  children?: ReactNode;
}): JSX.Element {
  const host = useRef<HTMLDivElement>(null);
  const [note, setNote] = useState<string | null>(null);
  const [gizmo, setGizmo] = useState<GizmoMode>(mode);
  const selectedRef = useRef(selected);
  const editsRef = useRef(edits);
  const basesRef = useRef(bases);
  const playbackRef = useRef(playback);
  const onSelectRef = useRef(onSelect);
  const onCommitRef = useRef(onCommit);
  const modeRef = useRef(gizmo);
  selectedRef.current = selected;
  editsRef.current = edits;
  basesRef.current = bases;
  playbackRef.current = playback;
  onSelectRef.current = onSelect;
  onCommitRef.current = onCommit;
  modeRef.current = gizmo;

  useEffect(() => { setGizmo(mode); }, [mode]);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.setClearColor(0x0e1116);
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f96, 0.7));
    const key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(1.2, 2.4, 1.6);
    scene.add(key);
    const grid = new THREE.GridHelper(2, 10, 0x263040, 0x1c2430);
    scene.add(grid);

    const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 100);
    camera.up.set(0, 1, 0);
    camera.position.set(1.2, 0.8, 1.2);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(0, 0.1, 0);

    const gizmoSize = 0.8;
    const transform = new TransformControls(camera, renderer.domElement);
    transform.setMode(modeRef.current);
    transform.setSize(gizmoSize);
    scene.add(transform);

    const meshes = new Map<string, THREE.Object3D>();
    const originals = new Map<string, Original>();
    const helper = new THREE.BoxHelper(new THREE.Object3D(), 0x4ea1ff);
    helper.visible = false;
    scene.add(helper);
    let dragging = false;

    const applyEdits = () => {
      if (dragging) return;
      for (const [name, object] of meshes) {
        const original = originals.get(name);
        if (!original) continue;
        object.position.copy(original.position);
        object.quaternion.copy(original.quaternion);
        object.scale.copy(original.scale);
        const edit = editsRef.current[name];
        if (!edit) continue;
        if (edit.location) {
          const [x, y, z] = zUpMmToYUpMetres(edit.location[0], edit.location[1], edit.location[2]);
          const world = new THREE.Vector3(x, y, z);
          object.parent?.worldToLocal(world);
          object.position.copy(world);
        }
        if (edit.rotation_deg) object.quaternion.copy(quaternionFromZUpEuler(edit.rotation_deg));
        const geometry = (object as THREE.Mesh).geometry;
        if (edit.dims && geometry) {
          geometry.computeBoundingBox();
          const box = geometry.boundingBox;
          if (box) {
            const spans = [
              (box.max.x - box.min.x) * 1000,
              (box.max.z - box.min.z) * 1000,
              (box.max.y - box.min.y) * 1000,
            ];
            const [sx, sy, sz] = axisScaleFactors(spans, basesRef.current[name] || {}, edit.dims);
            object.scale.set(original.scale.x * sx, original.scale.y * sz, original.scale.z * sy);
          }
        }
        object.updateMatrixWorld(true);
      }
      const poses = playbackRef.current;
      let partial = false;
      for (const [name, object] of meshes) {
        const pose = poses?.[name];
        if (pose) {
          object.position.y += pose.dropMm / 1000;
          paintOpacity(object, pose.opacity);
          if (pose.opacity < 0.98) partial = true;
        } else {
          paintOpacity(object, 1);
        }
      }
      const current = !partial && selectedRef.current ? meshes.get(selectedRef.current) : null;
      if (current) {
        helper.setFromObject(current);
        helper.visible = true;
        transform.attach(current);
      } else if (partial) {
        helper.visible = false;
        transform.detach();
      }
    };

    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    resize();

    let ignoreClick = false;
    transform.addEventListener("dragging-changed", (event) => {
      const active = (event as { value?: boolean }).value === true;
      controls.enabled = !active;
      dragging = active;
      if (active) return;
      ignoreClick = true;
      const object = transform.object;
      if (!object) return;
      object.updateMatrixWorld(true);
      const position = object.getWorldPosition(new THREE.Vector3());
      const rotation = zUpEulerFromQuaternion(object.getWorldQuaternion(new THREE.Quaternion()));
      onCommitRef.current(object.name, yUpMetresToZUpMm(position.x, position.y, position.z), rotation);
    });

    const ray = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const onPointer = (event: PointerEvent) => {
      if (ignoreClick) {
        ignoreClick = false;
        return;
      }
      if (dragging || transform.dragging || transform.axis) return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      ray.setFromCamera(pointer, camera);
      const hit = ray.intersectObjects([...meshes.values()], false)[0];
      onSelectRef.current(hit ? hit.object.name : null);
    };
    renderer.domElement.addEventListener("pointerup", onPointer);

    let frame = 0;
    let disposed = false;
    const loop = () => {
      if (disposed) return;
      const current = selectedRef.current ? meshes.get(selectedRef.current) : undefined;
      if (current && !dragging) helper.setFromObject(current);
      transform.setMode(modeRef.current);
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(loop);
    };
    loop();

    const projectClient = (world: THREE.Vector3) => {
      camera.updateMatrixWorld(true);
      const projected = world.clone().project(camera);
      const rect = renderer.domElement.getBoundingClientRect();
      return {
        x: rect.left + (projected.x + 1) * 0.5 * rect.width,
        y: rect.top + (1 - projected.y) * 0.5 * rect.height,
      };
    };
    const api = {
      apply: applyEdits,
      select(name: string | null) {
        const object = name ? meshes.get(name) : undefined;
        if (object) {
          helper.setFromObject(object);
          helper.visible = true;
          transform.attach(object);
        } else {
          helper.visible = false;
          transform.detach();
        }
      },
      /** Client coordinates of a part origin, plus an optional world-space offset in metres. */
      clientPoint(name: string, offset: [number, number, number] = [0, 0, 0]) {
        const object = meshes.get(name);
        if (!object) return null;
        object.updateMatrixWorld(true);
        const world = object.getWorldPosition(new THREE.Vector3());
        world.x += offset[0];
        world.y += offset[1];
        world.z += offset[2];
        return projectClient(world);
      },
      /** World-space length of one gizmo unit, matching TransformControls handle scale. */
      handleScale() {
        const object = selectedRef.current ? meshes.get(selectedRef.current) : null;
        if (!object) return 0;
        const world = object.getWorldPosition(new THREE.Vector3());
        const distance = world.distanceTo(camera.position);
        const factor = distance * Math.min(1.9 * Math.tan((Math.PI * camera.fov) / 360) / camera.zoom, 7);
        return (factor * gizmoSize) / 4;
      },
    };
    (el as HTMLDivElement & { __make?: typeof api }).__make = api;

    if (url) {
      setNote("Loading model…");
      new GLTFLoader().load(url, (gltf) => {
        if (disposed) return;
        const model = gltf.scene;
        orientLoadedModel(model);
        scene.add(model);
        model.traverse((object) => {
          if (!(object as THREE.Mesh).isMesh || !object.name) return;
          const mesh = object as THREE.Mesh;
          if (Array.isArray(mesh.material)) mesh.material = mesh.material.map((material) => material.clone());
          else if (mesh.material) mesh.material = mesh.material.clone();
          meshes.set(object.name, object);
          originals.set(object.name, {
            position: object.position.clone(),
            quaternion: object.quaternion.clone(),
            scale: object.scale.clone(),
          });
        });
        applyEdits();
        api.select(selectedRef.current);
        fit(camera, controls, model);
        grid.scale.setScalar(Math.max(new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3()).length(), 0.4));
        setNote(null);
      }, undefined, () => {
        if (!disposed) setNote("The model did not load.");
      });
    } else {
      setNote(null);
    }

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerup", onPointer);
      transform.dispose();
      controls.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, [url]);

  useEffect(() => {
    const api = (host.current as HTMLDivElement & { __make?: { apply: () => void; select: (name: string | null) => void } } | null)?.__make;
    api?.select(selected);
    api?.apply();
  }, [selected, edits, bases, playback]);

  return (
    <div className="make-view" ref={host} data-make="viewport">
      <div className="make-gizmo">
        <button type="button" className={gizmo === "translate" ? "on" : ""} data-make="gizmo-move" onClick={() => setGizmo("translate")}>Move</button>
        <button type="button" className={gizmo === "rotate" ? "on" : ""} data-make="gizmo-rotate" onClick={() => setGizmo("rotate")}>Rotate</button>
      </div>
      {children}
      {note && <p className="make-empty" data-make="viewport-note">{note}</p>}
      {!url && !note && <p className="make-empty">The scene will appear here after the first build.</p>}
    </div>
  );
}
