/* Three.js viewport: studio-lit, textured, with a programmable camera.
   Semantics kept from the original port: picking, explode (y by install
   order), section clip (z), install-order visibility, layer visibility.
   Added: OrbitControls, image-based lighting, soft shadows, procedural
   materials (materials.ts) with true-scale UVs (uv.ts), and an API an agent
   can drive: flyTo / setView / isolate / playIntro. */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { SPECS, disposeTextures, makeMaterial, materialClassFor } from "./materials";
import type { MaterialClass } from "./materials";
import type { Scene as SceneData } from "./types";
import { boxProjectUVs } from "./uv";

const K = 0.01; // mm -> scene units (== app/units.py MM_PER_VIEW_UNIT)
const INTRO_MS = 520; // drop-in time per component
const GHOST = 0.12; // opacity of components outside an isolate() set

/* Up to n points spread over a mesh's vertices (world space), each pulled 2%
   toward their centroid so a ray stops just short of the surface. */
function surfacePoints(m: THREE.Mesh, n: number): THREE.Vector3[] {
  const pos = m.geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
  if (!pos || pos.count === 0) return [];
  m.updateMatrixWorld();
  const step = Math.max(1, Math.floor(pos.count / n));
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i < pos.count && pts.length < n; i += step) {
    pts.push(new THREE.Vector3().fromBufferAttribute(pos, i).applyMatrix4(m.matrixWorld));
  }
  const c = pts.reduce((a, p) => a.add(p), new THREE.Vector3()).divideScalar(pts.length);
  return pts.map((p) => p.lerp(c, 0.02));
}
const CUTBACK = 0.6; // staircase: last-installed part starts this far along z

/* Label text: the drawing's own short label when there is one, else the id. */
const ZONE_COLOR = 0x4ea1ff;
const ZONE_OPACITY = 0.12;

export function labelText(id: string, sourceLabel: string | null): string {
  if (sourceLabel && sourceLabel.length <= 32) return sourceLabel;
  const t = id.replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export type ViewPreset = "iso" | "front" | "top" | "side";

const VIEW_DIRS: Record<ViewPreset, THREE.Vector3> = {
  iso: new THREE.Vector3(Math.cos(0.32) * Math.sin(-0.7), Math.sin(0.32), Math.cos(0.32) * Math.cos(-0.7)),
  front: new THREE.Vector3(0, 0.12, 1).normalize(), // looking at the section face
  top: new THREE.Vector3(0, 1, 0.03).normalize(),
  side: new THREE.Vector3(1, 0.15, 0.25).normalize(),
};

// rooms are drawn from the back-left inside corner: look in from the open front-right
const ROOM_ISO = new THREE.Vector3(0.72, 0.62, 0.95).normalize();

const clamp01 = (t: number) => Math.max(0, Math.min(1, t));
const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);
const reducedMotion = () =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

interface Tween {
  fromPos: THREE.Vector3;
  toPos: THREE.Vector3;
  fromTgt: THREE.Vector3;
  toTgt: THREE.Vector3;
  t0: number;
  ms: number;
}

export class Viewport3D {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private root = new THREE.Group();
  private key = new THREE.DirectionalLight(0xffffff, 1.6);
  private ground: THREE.Mesh;
  private env: THREE.Texture;
  private clipPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 1e9);
  private meshes = new Map<string, THREE.Mesh>();
  private baseY = new Map<string, number>();
  private ray = new THREE.Raycaster();
  private ndc = new THREE.Vector2();
  private radius = 6;
  private raf = 0;
  private disposed = false;
  private container: HTMLElement;
  private onPick: (id: string) => void;
  private resizeObs: ResizeObserver | null = null;
  private down: { x: number; y: number } | null = null;
  private detailId: string | null = null;
  private order: string[] = [];
  private visible: Record<string, boolean> = {};
  private seqOn = false;
  private seqIdx = -1;
  private explode = 0;
  private focusSet: Set<string> | null = null;
  private focusTarget: string[] | null = null; // parts the camera was sent to
  private occluders = new Set<string>(); // parts between the camera and focusTarget
  private occludedAt = 0;
  private occludedKey = ""; // focus + camera when occluders were last computed
  private clipOn = false;
  private selected: string | null = null;
  private tween: Tween | null = null;
  private intro: { t0: number; stepMs: number } | null = null;
  private labels = new CSS2DRenderer();
  private tags = new Map<string, CSS2DObject>();
  private showLabels = true;
  private cutaway = true;
  private section = true; // room-kind details are not cut back
  private extent = 6; // extrusion length, scene units
  private base = new Map<string, Float32Array>(); // uncut positions

  constructor(container: HTMLElement, onPick: (id: string) => void) {
    this.container = container;
    this.onPick = onPick;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.localClippingEnabled = true;
    this.renderer.outputEncoding = THREE.sRGBEncoding;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.92;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.insertBefore(this.renderer.domElement, container.firstChild);
    const ov = this.labels.domElement;
    ov.className = "labels";
    container.insertBefore(ov, this.renderer.domElement.nextSibling);

    this.scene.background = new THREE.Color(0xe6e9ed);
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    pmrem.dispose();
    this.scene.environment = this.env;
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f96, 0.25));
    this.key.castShadow = true;
    this.key.shadow.mapSize.set(2048, 2048);
    this.key.shadow.bias = -0.0004;
    this.key.shadow.normalBias = 0.02;
    this.scene.add(this.key, this.key.target);

    this.ground = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.ShadowMaterial({ opacity: 0.16 }),
    );
    this.ground.rotation.x = -Math.PI / 2;
    this.ground.receiveShadow = true;
    this.scene.add(this.ground, this.root);

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.01, 500);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.controls.screenSpacePanning = true;
    this.controls.addEventListener("start", () => (this.tween = null));
    this.camera.position.copy(VIEW_DIRS.iso).multiplyScalar(this.radius * 1.7);

    const cv = this.renderer.domElement;
    cv.addEventListener("pointerdown", this.onDown);
    cv.addEventListener("click", this.onClick);
    this.resizeObs = new ResizeObserver(() => this.onResize());
    this.resizeObs.observe(container);
    this.onResize();
    const loop = (now: number) => {
      if (this.disposed) return;
      this.step(now);
      this.controls.update();
      this.updateOccluders(now);
      this.renderer.render(this.scene, this.camera);
      this.labels.render(this.scene, this.camera);
      this.declutter();
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    const cv = this.renderer.domElement;
    cv.removeEventListener("pointerdown", this.onDown);
    cv.removeEventListener("click", this.onClick);
    this.resizeObs?.disconnect();
    this.controls.dispose();
    this.clearMeshes();
    this.ground.geometry.dispose();
    (this.ground.material as THREE.Material).dispose();
    this.env.dispose();
    disposeTextures();
    this.renderer.dispose();
    this.labels.domElement.remove();
    cv.remove();
  }

  /* ---------------------------------------------------------------- input */

  private onDown = (e: PointerEvent): void => {
    this.down = { x: e.clientX, y: e.clientY };
  };

  private onClick = (e: MouseEvent): void => {
    if (this.down && Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) > 5) return;
    const r = this.renderer.domElement.getBoundingClientRect();
    this.ndc.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    this.ndc.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    this.ray.setFromCamera(this.ndc, this.camera);
    const hit = this.ray.intersectObjects(
      [...this.meshes.values()].filter((m) => m.visible && (m.material as THREE.Material).opacity > GHOST + 0.01),
    )[0];
    if (hit) this.onPick((hit.object.userData as { id: string }).id);
  };

  private onResize = (): void => {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.labels.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  };

  /* ---------------------------------------------------------------- scene */

  private clearMeshes(): void {
    this.meshes.forEach((m) => {
      this.root.remove(m);
      m.geometry.dispose();
      (m.material as THREE.Material).dispose();
    });
    this.meshes.clear();
    this.focusTarget = null;
    this.occluders.clear();
    this.baseY.clear();
    this.tags.forEach((t) => t.element.remove());
    this.tags.clear();
    this.base.clear();
  }

  loadScene(s: SceneData, opts: { intro?: boolean } = {}): void {
    const sameDetail = s.detail_id === this.detailId;
    this.detailId = s.detail_id;
    this.clearMeshes();
    this.root.position.set(0, 0, 0);
    this.order = s.install_order;
    this.extent = Number(s.context.illustrative_extent_z_mm || 600) * K;
    this.section = s.context.kind !== "room";
    for (const solid of s.solids) {
      if (!solid.tris || !solid.tris.length) continue;
      const cls = materialClassFor(solid.layer, solid.name);
      const g = new THREE.BufferGeometry();
      const pos = new Float32Array(solid.tris.length);
      for (let i = 0; i < solid.tris.length; i++) pos[i] = solid.tris[i] * K;
      g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      g.setAttribute("uv", new THREE.BufferAttribute(boxProjectUVs(solid.tris, SPECS[cls].repeatMm), 2));
      g.computeVertexNormals();
      const idx = Math.max(0, this.order.indexOf(solid.id));
      if (solid.representation === "zone") {
        // Drawn but not built (a clearance, set-out line or datum): a faint
        // volume with outlined edges, no shadow, never mistaken for a part.
        const zoneMat = new THREE.MeshBasicMaterial({ color: ZONE_COLOR, transparent: true, opacity: ZONE_OPACITY,
          depthWrite: false, side: THREE.DoubleSide, clippingPlanes: [this.clipPlane] });
        const zone = new THREE.Mesh(g, zoneMat);
        zone.add(new THREE.LineSegments(new THREE.EdgesGeometry(g, 30),
          new THREE.LineBasicMaterial({ color: ZONE_COLOR, transparent: true, opacity: 0.8, clippingPlanes: [this.clipPlane] })));
        zone.userData = { id: solid.id, cls, baseOpacity: ZONE_OPACITY };
        this.meshes.set(solid.id, zone);
        this.root.add(zone);
        this.base.set(solid.id, pos.slice());
        zone.add(this.makeTag(solid.id, `${labelText(solid.id, solid.source_label)} · zone (not built)`, solid.source_label));
        continue;
      }
      const mat = makeMaterial(cls, solid.color);
      mat.envMapIntensity = cls === "ceramic" || cls === "glass" ? 0.9 : 0.45;
      mat.side = THREE.DoubleSide; // generated shapes: never lose a face to winding
      mat.clippingPlanes = [this.clipPlane];
      mat.clipShadows = true;
      mat.polygonOffset = true; // coplanar contacts: later installs draw on top
      mat.polygonOffsetFactor = -idx;
      mat.polygonOffsetUnits = -idx;
      const mesh = new THREE.Mesh(g, mat);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData = { id: solid.id, cls, baseOpacity: SPECS[cls].opacity ?? 1 };
      this.meshes.set(solid.id, mesh);
      this.root.add(mesh);
      this.base.set(solid.id, pos.slice());
      const label = labelText(solid.id, solid.source_label);
      mesh.add(this.makeTag(solid.id, solid.placeholder ? `${label} · placeholder (CAD ${solid.placeholder})` : label, solid.source_label));
    }
    this.applyCutaway();
    const box = new THREE.Box3().setFromObject(this.root);
    if (box.isEmpty()) return;
    const ctr = box.getCenter(new THREE.Vector3());
    this.root.position.sub(ctr);
    this.meshes.forEach((m, id) => this.baseY.set(id, m.position.y));
    const size = box.getSize(new THREE.Vector3());
    this.radius = Math.max(size.x, size.y, size.z);
    const r = this.radius;
    this.ground.position.y = box.min.y - ctr.y - 0.002;
    this.ground.scale.set(r * 8, r * 8, 1);
    this.key.position.set(r * 0.8, r * 2.0, r * 1.3);
    const sc = this.key.shadow.camera;
    sc.left = sc.bottom = -r * 1.3;
    sc.right = sc.top = r * 1.3;
    sc.near = 0.1;
    sc.far = r * 6;
    sc.updateProjectionMatrix();
    this.controls.minDistance = r * 0.03;
    this.controls.maxDistance = r * 5;
    this.focusSet = null;
    this.applyVisibility();
    this.layout();
    this.setSelected(this.selected);
    if (!sameDetail) this.frame(null, this.isoDir(), 0.95, 0);
    if (opts.intro) this.playIntro();
  }

  /** Remove the current detail (e.g. a drawing with no detail selected). */
  clear(): void {
    this.clearMeshes();
    this.detailId = null;
    this.order = [];
  }

  setSelected(id: string | null): void {
    this.selected = id;
    this.meshes.forEach((mesh, cid) => {
      const m = mesh.material as THREE.MeshStandardMaterial;
      if (m.emissive) { // zones use a basic material: their tag shows the selection
        m.emissive.set(cid === id ? 0x2563eb : 0x000000);
        m.emissiveIntensity = cid === id ? 0.35 : 0;
      }
      this.tags.get(cid)?.element.classList.toggle("sel", cid === id);
    });
    this.layout();
  }

  setState(v: {
    visible: Record<string, boolean>;
    seqOn: boolean;
    seqIdx: number;
    explode: number;
    clipOn: boolean;
    clipZ: number;
  }): void {
    this.visible = v.visible;
    this.seqOn = v.seqOn;
    this.seqIdx = v.seqIdx;
    this.explode = v.explode;
    this.clipOn = v.clipOn;
    this.applyVisibility();
    this.layout();
    this.clipPlane.constant = v.clipOn ? v.clipZ * K + this.root.position.z : 1e9;
  }

  private applyVisibility(): void {
    this.meshes.forEach((m, id) => {
      let vis = this.visible[id] !== false;
      if (this.seqOn) {
        const i = this.order.indexOf(id);
        vis = vis && i >= 0 && i <= this.seqIdx;
      }
      m.visible = vis;
    });
  }

  /* Positions (explode + intro drop) and opacity (intro fade + isolate). */
  private layout(now = performance.now()): void {
    const f = this.explode * K;
    let introDone = true;
    this.order.forEach((id, i) => {
      const m = this.meshes.get(id);
      if (!m) return;
      let lift = 0;
      let alpha = 1;
      if (this.intro) {
        const p = clamp01((now - this.intro.t0 - i * this.intro.stepMs) / INTRO_MS);
        if (p < 1) introDone = false;
        lift = (1 - easeOut(p)) * this.radius * 0.3;
        alpha = p;
      }
      m.position.y = (this.baseY.get(id) || 0) + (i * f) / 8 + lift;
      const ghosted = (this.focusSet !== null && !this.focusSet.has(id)) || this.occluders.has(id);
      const base = (m.userData as { baseOpacity: number }).baseOpacity;
      const opacity = Math.min(alpha, ghosted ? GHOST : base);
      const mat = m.material as THREE.MeshStandardMaterial;
      if (mat.transparent !== opacity < 1) {
        mat.transparent = opacity < 1;
        mat.needsUpdate = true;
      }
      mat.opacity = opacity;
      mat.depthWrite = opacity >= 1;
      m.castShadow = opacity >= 1;
      const full = opacity >= Math.min(1, base);
      const tag = this.tags.get(id);
      if (tag) tag.visible = m.visible && full && (this.showLabels || id === this.selected);
    });
    if (this.intro && introDone) this.intro = null;
  }

  /* ---------------------------------------------------------------- camera */

  private step(now: number): void {
    if (this.intro) this.layout(now);
    const t = this.tween;
    if (!t) return;
    const p = clamp01((now - t.t0) / t.ms);
    const e = easeInOut(p);
    this.camera.position.lerpVectors(t.fromPos, t.toPos, e);
    this.controls.target.lerpVectors(t.fromTgt, t.toTgt, e);
    if (p >= 1) this.tween = null;
  }

  private frame(ids: string[] | null, dir: THREE.Vector3, pad: number, ms: number): void {
    this.root.updateMatrixWorld(true);
    const box = new THREE.Box3();
    const list = ids && ids.length ? ids : [...this.meshes.keys()];
    for (const id of list) {
      const m = this.meshes.get(id);
      if (m) box.expandByObject(m);
    }
    if (box.isEmpty()) return;
    const ctr = box.getCenter(new THREE.Vector3());
    const r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, this.radius * 0.04);
    const fit = (r / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2))) * pad;
    const toPos = ctr.clone().add(dir.clone().normalize().multiplyScalar(fit));
    if (ms <= 0 || reducedMotion()) {
      this.tween = null;
      this.camera.position.copy(toPos);
      this.controls.target.copy(ctr);
      return;
    }
    this.tween = {
      fromPos: this.camera.position.clone(),
      toPos,
      fromTgt: this.controls.target.clone(),
      toTgt: ctr,
      t0: performance.now(),
      ms,
    };
  }

  /* Parts between the camera and the parts it was sent to fade, so a wall
     never hides what the camera is looking at (also while orbiting). Rays go
     to points on the part's own surface, not its bounding-box centre, which
     for an L-shaped part can be empty space. Recomputed only when the focus
     or the camera moves. */
  private updateOccluders(now: number): void {
    if (now - this.occludedAt < 100) return;
    this.occludedAt = now;
    const focus = this.focusTarget;
    const cam = this.camera.position;
    const key = focus
      ? [focus.join(","), cam.x, cam.y, cam.z, this.explode, this.seqOn, this.seqIdx]
          .map((v) => (typeof v === "number" ? v.toFixed(3) : String(v)))
          .join("|")
      : "";
    if (key === this.occludedKey) return;
    this.occludedKey = key;
    const next = new Set<string>();
    if (focus) {
      const others = [...this.meshes.entries()]
        .filter(([id, m]) => m.visible && !focus.includes(id))
        .map(([, m]) => m);
      for (const id of focus) {
        const m = this.meshes.get(id);
        if (!m || !m.visible) continue;
        for (const p of surfacePoints(m, 16)) {
          const to = p.sub(cam);
          const dist = to.length();
          this.ray.set(cam, to.divideScalar(dist));
          this.ray.far = dist * 0.995; // not the face the part sits against
          for (const hit of this.ray.intersectObjects(others, false)) {
            next.add((hit.object.userData as { id: string }).id);
          }
        }
      }
      this.ray.far = Infinity;
    }
    if (next.size !== this.occluders.size || [...next].some((id) => !this.occluders.has(id))) {
      this.occluders = next;
      this.layout(now);
    }
  }

  /** Fly the camera to frame these components (all when null/empty),
      keeping the current viewing direction unless a preset is given. */
  flyTo(ids: string[] | null, opts: { view?: ViewPreset; ms?: number; pad?: number } = {}): void {
    this.focusTarget = ids && ids.length ? [...ids] : null;
    const dir = opts.view
      ? opts.view === "iso" ? this.isoDir() : VIEW_DIRS[opts.view]
      : this.camera.position.clone().sub(this.controls.target).normalize();
    this.frame(ids, dir, opts.pad ?? (ids && ids.length ? 1.4 : 0.95), opts.ms ?? 900);
  }

  /** True when the view shows the whole detail as built: nothing selected,
      isolated, focused, exploded, cut, stepped or moving. */
  pristine(): boolean {
    return (
      !this.intro &&
      !this.tween &&
      this.selected === null &&
      this.focusSet === null &&
      this.focusTarget === null &&
      this.explode === 0 &&
      !this.seqOn &&
      !this.clipOn
    );
  }

  setView(view: ViewPreset, ms = 900): void {
    this.focusTarget = null;
    this.frame(null, view === "iso" ? this.isoDir() : VIEW_DIRS[view], 0.95, ms);
  }

  private isoDir(): THREE.Vector3 {
    return this.section ? VIEW_DIRS.iso : ROOM_ISO;
  }

  /** Show only these components at full opacity; the rest are ghosted. */
  isolate(ids: string[] | null): void {
    this.focusSet = ids && ids.length ? new Set(ids) : null;
    this.layout();
  }

  /** Build-up animation: components drop in, in install order. */
  playIntro(stepMs?: number): void {
    if (reducedMotion() || !this.order.length) return;
    const each = stepMs ?? Math.max(90, Math.min(260, 2600 / this.order.length));
    this.intro = { t0: performance.now(), stepMs: each };
    this.layout();
  }

  /* Leader-label stacking: place chips in priority order; on overlap, lift
     the chip on a longer stem (up to 6 levels), else hide it (dot remains). */
  private declutter(): void {
    const W = this.renderer.domElement.clientWidth;
    const H = this.renderer.domElement.clientHeight;
    const v = new THREE.Vector3();
    const placed: number[][] = [];
    const rank = (id: string) =>
      (id === this.selected ? 0 : 1e4) +
      ((this.tags.get(id)!.userData as { drawn: boolean }).drawn ? 0 : 1e3) -
      this.order.indexOf(id);
    const ids = [...this.tags.keys()].filter((id) => this.tags.get(id)!.visible);
    ids.sort((a, b) => rank(a) - rank(b));
    for (const id of ids) {
      const t = this.tags.get(id)!;
      const chip = t.element.firstChild as HTMLElement;
      const stem = t.element.lastChild as HTMLElement;
      t.getWorldPosition(v).project(this.camera);
      const sx = ((v.x + 1) / 2) * W;
      const sy = ((1 - v.y) / 2) * H;
      const w = chip.offsetWidth;
      const h = chip.offsetHeight;
      let lift = -1;
      for (let lvl = 0; lvl < 6 && lift < 0; lvl++) {
        const st = 16 + lvl * 22;
        const r = [sx - w / 2 - 3, sy - st - h - 3, sx + w / 2 + 3, sy - st + 3];
        if (!placed.some((o) => r[0] < o[2] && r[2] > o[0] && r[1] < o[3] && r[3] > o[1])) {
          placed.push(r);
          lift = st;
        }
      }
      chip.style.visibility = lift < 0 ? "hidden" : "";
      stem.style.visibility = lift < 0 ? "hidden" : "";
      if (lift >= 0) {
        chip.style.bottom = `${lift}px`;
        stem.style.height = `${lift}px`;
      }
    }
  }

  private makeTag(id: string, text: string, full: string | null): CSS2DObject {
    const el = document.createElement("div");
    el.className = "tag";
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.textContent = text;
    if (full) chip.title = full;
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      this.onPick(id);
    });
    el.append(chip, Object.assign(document.createElement("i"), { className: "stem" }));
    const obj = new CSS2DObject(el);
    obj.userData = { drawn: !!full };
    this.tags.set(id, obj);
    return obj;
  }

  /* Staircase cutaway (stepped layer reveal on a real section): each extruded
     part starts further along z in install order, so every layer shows its
     own band while the front face keeps the full junction section. Only
     prisms extruded over the full extent are cut; anything else is left. */
  private applyCutaway(): void {
    const parts = this.order.filter((id) => this.meshes.has(id));
    const step = parts.length > 1 ? (this.extent * CUTBACK) / (parts.length - 1) : 0;
    const floorShift = this.cutaway && !this.section ? this.floorStaircase(parts) : new Map<string, [number, number]>();
    parts.forEach((id, i) => {
      const mesh = this.meshes.get(id)!;
      const src = this.base.get(id)!;
      const pos = (mesh.geometry.getAttribute("position") as THREE.BufferAttribute).array as Float32Array;
      let prism = true;
      for (let k = 2; k < src.length; k += 3) {
        if (Math.abs(src[k]) > 1e-6 && Math.abs(src[k] - this.extent) > 1e-6) {
          prism = false;
          break;
        }
      }
      const cut = this.cutaway && this.section && prism;
      const z0 = cut ? i * step : 0;
      const fx = floorShift.get(id);
      for (let k = 0; k < src.length; k++) {
        pos[k] = k % 3 === 2 && cut && Math.abs(src[k]) <= 1e-6 ? z0 : src[k];
        if (fx && k % 3 === 0 && Math.abs(src[k] - fx[0]) <= 1e-6) pos[k] = fx[1];
      }
      const g = mesh.geometry;
      g.getAttribute("position").needsUpdate = true;
      const mm = new Float32Array(pos.length);
      for (let k = 0; k < pos.length; k++) mm[k] = pos[k] / K;
      g.setAttribute("uv", new THREE.BufferAttribute(
        boxProjectUVs(mm, SPECS[(mesh.userData as { cls: MaterialClass }).cls].repeatMm), 2));
      g.computeVertexNormals();
      g.computeBoundingBox();
      g.computeBoundingSphere();
      // label: on top of this part's own exposed band
      const bb = g.boundingBox!;
      const zEnd = cut && i < parts.length - 1 ? (i + 1) * step : this.extent;
      const zMid = prism ? (z0 + Math.min(zEnd, this.extent)) / 2 : (bb.min.z + bb.max.z) / 2;
      this.tags.get(id)?.position.set((bb.min.x + bb.max.x) / 2, bb.max.y, zMid);
    });
  }

  /* Rooms: floor layers (thin, covering most of the floor) step back along x
     in install order, so each finish reveals the one beneath. */
  private floorStaircase(parts: string[]): Map<string, [number, number]> {
    const bounds = (a: Float32Array) => {
      const b = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
      for (let k = 0; k < a.length; k += 3) {
        for (let j = 0; j < 3; j++) {
          b[j] = Math.min(b[j], a[k + j]);
          b[j + 3] = Math.max(b[j + 3], a[k + j]);
        }
      }
      return b;
    };
    const bb = new Map(parts.map((id) => [id, bounds(this.base.get(id)!)]));
    const all = [...bb.values()];
    const minX = Math.min(...all.map((b) => b[0]));
    const maxX = Math.max(...all.map((b) => b[3]));
    const minZ = Math.min(...all.map((b) => b[2]));
    const maxZ = Math.max(...all.map((b) => b[5]));
    const floors = parts.filter((id) => {
      const b = bb.get(id)!;
      return b[4] - b[1] <= 60 * K && b[3] - b[0] >= 0.5 * (maxX - minX) && b[5] - b[2] >= 0.5 * (maxZ - minZ);
    });
    const out = new Map<string, [number, number]>();
    const step = floors.length > 1 ? ((maxX - minX) * 0.45) / (floors.length - 1) : 0;
    floors.forEach((id, i) => {
      if (i === 0) return;
      const b = bb.get(id)!;
      out.set(id, [b[0], Math.min(b[3] - step * 0.5, b[0] + i * step)]);
    });
    return out;
  }

  setCutaway(on: boolean): void {
    this.cutaway = on;
    this.applyCutaway();
  }

  setLabels(on: boolean): void {
    this.showLabels = on;
    this.layout();
  }

  /** End any running build-up animation (snapshots need the final state). */
  skipIntro(): void {
    this.intro = null;
    this.layout();
  }

  /** Render one view to a JPEG data URL with every visible part tagged by its
      id, so a model can say which part is wrong (tags=false for a clean
      library card). The live camera is restored. */
  snapshot(view: ViewPreset, maxW = 1024, tags = true): string {
    const pos = this.camera.position.clone();
    const tgt = this.controls.target.clone();
    const tween = this.tween; // a camera flight in progress continues afterwards
    const faded = this.occluders;
    this.occluders = new Set();
    this.skipIntro();
    this.frame(null, view === "iso" ? this.isoDir() : VIEW_DIRS[view], 0.95, 0);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    const gl = this.renderer.domElement;
    const k = Math.min(1, maxW / gl.width);
    const out = document.createElement("canvas");
    out.width = Math.round(gl.width * k);
    out.height = Math.round(gl.height * k);
    const ctx = out.getContext("2d")!;
    ctx.drawImage(gl, 0, 0, out.width, out.height);
    const px = out.width / Math.max(1, gl.clientWidth);
    ctx.font = `600 ${Math.max(10, Math.round(11 * px))}px system-ui, sans-serif`;
    ctx.textBaseline = "middle";
    const v = new THREE.Vector3();
    const placed: number[][] = [];
    const h = 16 * px;
    for (const [id, tag] of tags ? this.tags : []) {
      const m = this.meshes.get(id);
      if (!m || !m.visible || (m.material as THREE.Material).opacity <= GHOST + 0.01) continue;
      tag.getWorldPosition(v).project(this.camera);
      if (v.z < -1 || v.z > 1) continue;
      const x = ((v.x + 1) / 2) * out.width;
      const y = ((1 - v.y) / 2) * out.height;
      const w = ctx.measureText(id).width + 8 * px;
      let lift = 14 * px;
      const hit = (r: number[]) =>
        placed.some((o) => r[0] < o[2] && r[2] > o[0] && r[1] < o[3] && r[3] > o[1]);
      while (hit([x - w / 2, y - lift - h, x + w / 2, y - lift]) && lift < 160 * px) lift += 18 * px;
      placed.push([x - w / 2, y - lift - h, x + w / 2, y - lift]);
      ctx.strokeStyle = "rgba(31,41,55,.7)";
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x, y - lift);
      ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,.95)";
      ctx.fillRect(x - w / 2, y - lift - h, w, h);
      ctx.strokeRect(x - w / 2, y - lift - h, w, h);
      ctx.fillStyle = "#111827";
      ctx.fillText(id, x - w / 2 + 4 * px, y - lift - h / 2);
    }
    const url = out.toDataURL("image/jpeg", 0.85);
    this.occluders = faded;
    this.layout();
    this.camera.position.copy(pos);
    this.controls.target.copy(tgt);
    this.controls.update();
    this.tween = tween;
    return url;
  }

  componentIds(): string[] {
    return [...this.meshes.keys()];
  }
}
