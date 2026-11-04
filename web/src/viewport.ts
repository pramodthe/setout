/* The studio's 3D view: a Blender model in the browser, with its parts by name. It selects, hides, pulls apart and plays back the build, and never changes the model. */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";

export type Look = "material" | "clay";
export type View = "iso" | "other" | "perspective" | "front" | "side" | "top";
export type Tool = "select" | "pan" | "orbit";

const CLAY = new THREE.MeshStandardMaterial({ color: new THREE.Color(0xa4adb7).convertSRGBToLinear(), roughness: 0.92, metalness: 0 });
const HOVER = 0x3b82f6;
const CHOSEN = 0xf59e0b;
const STEP_MS = 650;
const FOV = 32;
const MARGIN = 1.22;  /* room left around a fitted view */
const THIN = 0.02;  /* metres */
const TALL = 0.6;  /* of the model's height */
const SLENDER = 0.15;  /* a tall part this thin, against its height, is left out of framing */

type Part = { mesh: THREE.Mesh; own: THREE.Material | THREE.Material[]; edges: THREE.LineSegments; home: THREE.Vector3; push: THREE.Vector3; step: number };

function release(root: THREE.Object3D, extra: THREE.Material[] = []): void {
  const materials = new Set(extra);
  const textures = new Set<THREE.Texture>();
  root.traverse((node) => {
    const mesh = node as THREE.Mesh;
    mesh.geometry?.dispose();
    if (mesh.material) (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).forEach((m) => materials.add(m));
  });
  for (const material of materials) {
    for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
    material.dispose();
  }
  textures.forEach((texture) => texture.dispose());
}

export class StudioViewport {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private persp = new THREE.PerspectiveCamera(FOV, 1, 0.01, 200);
  private ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 200);
  private camera: THREE.PerspectiveCamera | THREE.OrthographicCamera = this.ortho;
  private flatHeight = 1;
  private sun = new THREE.DirectionalLight(0xffffff, 0.95);
  private controls: OrbitControls;
  private root = new THREE.Group();
  private ground: THREE.Mesh;
  private parts = new Map<string, Part>();
  private hidden = new Set<string>();
  private box = new THREE.Box3();
  private focus = new THREE.Box3();
  private raycaster = new THREE.Raycaster();
  private pointer = new THREE.Vector2();
  private selected: string | null = null;
  private hovered: string | null = null;
  private tool: Tool = "select";
  private look: Look = "material";
  private spread = 0;
  private playing = 0;
  private playStep: number | null = null;
  private visible = true;
  private frameId = 0;
  private downAt: [number, number] | null = null;
  private observer: ResizeObserver;
  private disposed = false;

  private host: HTMLElement;
  private onPick: (name: string | null) => void;

  constructor(host: HTMLElement, onPick: (name: string | null) => void) {
    this.host = host;
    this.onPick = onPick;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true, logarithmicDepthBuffer: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputEncoding = THREE.sRGBEncoding;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.72;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    host.append(this.renderer.domElement);
    const environment = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = environment.fromScene(new RoomEnvironment(), 0.04).texture;
    const sun = this.sun;
    sun.position.set(-4, 6, 3);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0004;
    sun.shadow.radius = 3;
    this.scene.add(sun, sun.target);
    this.ground = new THREE.Mesh(new THREE.PlaneGeometry(30, 30), new THREE.ShadowMaterial({ opacity: 0.12 }));
    this.ground.rotation.x = -Math.PI / 2;
    this.ground.receiveShadow = true;
    this.scene.add(this.root, this.ground);
    this.ortho.up.set(0, 1, 0);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.setTool("select");
    const canvas = this.renderer.domElement;
    canvas.addEventListener("pointerdown", (e) => { this.downAt = [e.clientX, e.clientY]; });
    canvas.addEventListener("pointerup", (e) => this.click(e));
    canvas.addEventListener("pointermove", (e) => this.hover(e));
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(host);
    this.resize();
    const loop = () => { this.frameId = requestAnimationFrame(loop); if (this.visible) { this.controls.update(); this.renderer.render(this.scene, this.camera); } };
    loop();
  }

  dispose(): void {
    this.disposed = true;
    this.stopPlay();
    cancelAnimationFrame(this.frameId);
    this.observer.disconnect();
    this.controls.dispose();
    release(this.scene, [...this.parts.values()].flatMap((part) => Array.isArray(part.own) ? part.own : [part.own]));
    this.scene.environment?.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  private resize(): void {
    const { clientWidth: w, clientHeight: h } = this.host;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.persp.aspect = w / h;
    this.persp.updateProjectionMatrix();
    this.fitOrtho();
  }

  async load(url: string, preserveView = false): Promise<void> {
    const gltf = await new GLTFLoader().loadAsync(url);
    if (this.disposed) { release(gltf.scene); return; }
    release(this.root, [...this.parts.values()].flatMap((part) => Array.isArray(part.own) ? part.own : [part.own]));
    this.root.clear();
    this.parts.clear();
    if (!preserveView) this.hidden.clear();
    this.root.add(gltf.scene);
    gltf.scene.updateMatrixWorld(true);
    this.box.setFromObject(gltf.scene);
    this.focus = this.detailBox(gltf.scene);
    const centre = this.focus.getCenter(new THREE.Vector3());
    const reach = this.focus.getSize(new THREE.Vector3()).length();
    const edge = new THREE.LineBasicMaterial({ color: 0x17181a, transparent: true, opacity: 0.52 });
    gltf.scene.traverse((node) => {
      const mesh = node as THREE.Mesh;
      if (!mesh.isMesh) return;
      const name = this.partName(mesh);
      mesh.castShadow = mesh.receiveShadow = true;
      /* Each part has its own material, so one can light up alone. A thin layer lying on something draws just in front of it, or the two flicker where they meet. */
      const thin = new THREE.Box3().setFromObject(mesh).getSize(new THREE.Vector3()).toArray().sort((a, b) => a - b)[0] < THIN;
      const own = (m: THREE.Material) => {
        const held = m.clone() as THREE.MeshStandardMaterial;
        for (const texture of [held.map, held.normalMap, held.roughnessMap]) if (texture) texture.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
        held.side = THREE.DoubleSide;
        held.envMapIntensity = 0.55;
        held.polygonOffset = thin;
        held.polygonOffsetFactor = held.polygonOffsetUnits = -2;
        return held;
      };
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(own) : own(mesh.material);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 32), edge);
      mesh.add(edges);
      const middle = new THREE.Box3().setFromObject(mesh).getCenter(new THREE.Vector3());
      const home = mesh.position.clone();
      const out = middle.clone().sub(centre);
      out.x = 0;
      const push = (out.lengthSq() > 1e-8 ? out.normalize() : new THREE.Vector3(0, 1, 0)).multiplyScalar(reach * 0.22 * Math.min(out.length() / (reach / 2) + 0.25, 1.2));
      this.parts.set(this.parts.has(name) ? `${name} ${mesh.uuid}` : name, { mesh, own: mesh.material, edges, home, push, step: Number(this.extras(mesh).step ?? 0) });
    });
    this.ground.position.y = this.box.min.y - 0.002;
    this.setLook(this.look);
    this.apply();
    if (!preserveView) this.setView("iso");
  }

  /* What to frame: the whole model, less anything tall and thin standing above the rest, such as a door over a sill. A tall mass of concrete is the detail, and stays. */
  private detailBox(model: THREE.Object3D): THREE.Box3 {
    const height = this.box.max.y - this.box.min.y;
    const low = new THREE.Box3();
    model.traverse((node) => {
      if (!(node as THREE.Mesh).isMesh) return;
      const box = new THREE.Box3().setFromObject(node);
      const size = box.getSize(new THREE.Vector3());
      if (size.y < TALL * height || Math.min(size.x, size.z) > SLENDER * size.y) low.union(box);
    });
    return low.isEmpty() ? this.box.clone() : low;
  }

  private extras(mesh: THREE.Object3D): Record<string, unknown> {
    return (Object.keys(mesh.userData).length ? mesh.userData : mesh.parent?.userData ?? {}) as Record<string, unknown>;
  }

  /* A mesh with several materials loads as a group of meshes under the object's name. */
  private partName(mesh: THREE.Mesh): string {
    const own = mesh.userData.name as string | undefined;
    return own ?? mesh.name;
  }

  private apply(): void {
    for (const [name, part] of this.parts) {
      part.mesh.visible = !this.hidden.has(name) && (this.playStep === null || part.step <= this.playStep);
      part.mesh.position.copy(part.home).addScaledVector(part.push, this.spread);
      this.tint(name);
    }
  }

  private tint(name: string): void {
    const part = this.parts.get(name);
    if (!part) return;
    const chosen = name === this.selected;
    const colour = chosen ? CHOSEN : name === this.hovered ? HOVER : 0x000000;
    const strength = chosen ? 0.42 : name === this.hovered ? 0.24 : 0;
    const paint = (m: THREE.MeshStandardMaterial) => {
      m.emissive.setHex(colour);
      m.emissiveIntensity = strength;
    };
    if (Array.isArray(part.mesh.material)) (part.mesh.material as THREE.MeshStandardMaterial[]).forEach(paint); else paint(part.mesh.material as THREE.MeshStandardMaterial);
  }

  setLook(look: Look): void {
    this.look = look;
    for (const part of this.parts.values()) {
      if (look === "clay") {
        const clay = CLAY.clone();
        clay.side = THREE.DoubleSide;
        clay.polygonOffset = (Array.isArray(part.own) ? part.own[0] : part.own).polygonOffset;
        clay.polygonOffsetFactor = clay.polygonOffsetUnits = -2;
        part.mesh.material = clay;
      } else {
        part.mesh.material = part.own;
      }
    }
    this.apply();
  }

  setTool(tool: Tool): void {
    this.tool = tool;
    this.controls.mouseButtons = { LEFT: tool === "pan" ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
  }

  setVisible(visible: boolean): void { this.visible = visible; this.controls.enabled = visible; }

  setSpread(share: number): void { this.spread = share; this.apply(); }

  setHidden(names: string[], hide: boolean): void {
    names.forEach((n) => (hide ? this.hidden.add(n) : this.hidden.delete(n)));
    this.apply();
  }

  select(name: string | null): void {
    const before = this.selected;
    this.selected = name;
    if (before) this.tint(before);
    if (name) this.tint(name);
  }

  /* Isometric and the flat views have no perspective, so they read like drawings from different sides. */
  setView(view: View): void {
    const flat = view === "front" || view === "side" || view === "top";
    const shown = flat ? this.box : this.focus;
    const middle = shown.getCenter(new THREE.Vector3());
    const reach = shown.getSize(new THREE.Vector3()).length() / 2;
    const away = {
      iso: new THREE.Vector3(-1, 1, 1),
      other: new THREE.Vector3(-1, 1, -1),
      perspective: new THREE.Vector3(-1.0, 0.62, 0.72),
      front: new THREE.Vector3(-1, 0, 0),
      side: new THREE.Vector3(0, 0, 1),
      top: new THREE.Vector3(0, 1, 0.0001),
    }[view].normalize();
    this.ground.visible = !flat;
    this.sun.castShadow = !flat;  /* a flat view is a drawing, and shadows only mark it */
    this.camera = view === "perspective" ? this.persp : this.ortho;
    this.controls.object = this.camera;
    this.controls.target.copy(middle);
    if (view === "perspective") {
      const distance = (1.5 * reach) / Math.sin(THREE.MathUtils.degToRad(FOV / 2)) * 0.72;
      this.camera.position.copy(middle).addScaledVector(away, distance);
      this.persp.near = distance / 100;
      this.persp.far = distance * 12;
      this.persp.updateProjectionMatrix();
    } else {
      this.camera.position.copy(middle).addScaledVector(away, reach * 6);
      this.camera.lookAt(middle);
      this.camera.updateMatrixWorld();
      const seen = new THREE.Box3();
      for (const x of [shown.min.x, shown.max.x]) for (const y of [shown.min.y, shown.max.y]) for (const z of [shown.min.z, shown.max.z]) {
        seen.expandByPoint(new THREE.Vector3(x, y, z).applyMatrix4(this.camera.matrixWorldInverse));
      }
      const wide = seen.max.x - seen.min.x;
      const high = seen.max.y - seen.min.y;
      const aspect = this.host.clientWidth / Math.max(this.host.clientHeight, 1);
      this.flatHeight = (Math.max(high, wide / aspect) / 2) * MARGIN;
      this.ortho.zoom = 1;
      this.ortho.near = reach / 100;
      this.ortho.far = reach * 14;
      this.fitOrtho();
    }
    this.controls.update();
  }

  private fitOrtho(): void {
    const aspect = this.host.clientWidth / Math.max(this.host.clientHeight, 1);
    this.ortho.top = this.flatHeight;
    this.ortho.bottom = -this.flatHeight;
    this.ortho.left = -this.flatHeight * aspect;
    this.ortho.right = this.flatHeight * aspect;
    this.ortho.updateProjectionMatrix();
  }

  /* Plays the build, step by step. Resolves when it finishes or is stopped. */
  async play(onStep: (step: number) => void): Promise<void> {
    const run = ++this.playing;
    const steps = [...new Set([...this.parts.values()].map((p) => p.step))].sort((a, b) => a - b);
    this.playStep = 0;
    this.apply();
    for (const step of steps) {
      if (run !== this.playing) return;
      this.playStep = step;
      onStep(step);
      this.apply();
      await new Promise((done) => setTimeout(done, STEP_MS));
    }
    if (run !== this.playing) return;
    this.playStep = null;
    this.apply();
    onStep(-1);
  }

  stopPlay(): void { this.playing++; this.playStep = null; this.apply(); }

  private pick(e: PointerEvent): string | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const meshes = [...this.parts.values()].map((p) => p.mesh).filter((m) => m.visible);
    const hit = this.raycaster.intersectObjects(meshes, false)[0];
    if (!hit) return null;
    return [...this.parts.entries()].find(([, p]) => p.mesh === hit.object)?.[0] ?? null;
  }

  private click(e: PointerEvent): void {
    if (this.tool !== "select" || !this.downAt) return;
    const moved = Math.hypot(e.clientX - this.downAt[0], e.clientY - this.downAt[1]);
    this.downAt = null;
    if (moved > 4) return;
    const name = this.pick(e);
    this.select(name);
    this.onPick(name);
  }

  private hover(e: PointerEvent): void {
    if (e.buttons || this.tool !== "select") return;
    const name = this.pick(e);
    if (name === this.hovered) return;
    const before = this.hovered;
    this.hovered = name;
    if (before) this.tint(before);
    if (name) this.tint(name);
    this.renderer.domElement.style.cursor = name ? "pointer" : "grab";
  }
}
