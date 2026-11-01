/* The studio's 3D view: a Blender model in the browser, with its parts by name. It selects, hides, pulls apart, slices and plays back the build, and never changes the model. */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";

export type Look = "material" | "clay";
export type View = "iso" | "other" | "section" | "top";
export type Tool = "select" | "pan" | "orbit";

const CLAY = new THREE.MeshStandardMaterial({ color: 0xb9b6ae, roughness: 0.92, metalness: 0 });
const HOVER = 0x3b82f6;
const CHOSEN = 0xf59e0b;
const STEP_MS = 650;
const FOV = 32;
const FLAT_FOV = 5;
const THIN = 0.02;  /* metres */
const TALL = 0.6;  /* of the model's height */

type Part = { mesh: THREE.Mesh; own: THREE.Material | THREE.Material[]; edges: THREE.LineSegments; home: THREE.Vector3; push: THREE.Vector3; step: number };

export class StudioViewport {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(FOV, 1, 0.01, 200);
  private controls: OrbitControls;
  private root = new THREE.Group();
  private ground: THREE.Mesh;
  private parts = new Map<string, Part>();
  private hidden = new Set<string>();
  private box = new THREE.Box3();
  private focus = new THREE.Box3();
  private slice = new THREE.Plane(new THREE.Vector3(1, 0, 0), 1000);
  private raycaster = new THREE.Raycaster();
  private pointer = new THREE.Vector2();
  private selected: string | null = null;
  private hovered: string | null = null;
  private tool: Tool = "select";
  private look: Look = "material";
  private spread = 0;
  private playing = 0;
  private frameId = 0;
  private downAt: [number, number] | null = null;
  private observer: ResizeObserver;

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
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.localClippingEnabled = true;
    host.append(this.renderer.domElement);
    const environment = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = environment.fromScene(new RoomEnvironment(), 0.04).texture;
    const sun = new THREE.DirectionalLight(0xffffff, 0.95);
    sun.position.set(-4, 6, 3);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0004;
    this.scene.add(sun, sun.target);
    this.ground = new THREE.Mesh(new THREE.PlaneGeometry(30, 30), new THREE.ShadowMaterial({ opacity: 0.32 }));
    this.ground.rotation.x = -Math.PI / 2;
    this.ground.receiveShadow = true;
    this.scene.add(this.root, this.ground);
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
    const loop = () => { this.frameId = requestAnimationFrame(loop); this.controls.update(); this.renderer.render(this.scene, this.camera); };
    loop();
  }

  dispose(): void {
    cancelAnimationFrame(this.frameId);
    this.observer.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  private resize(): void {
    const { clientWidth: w, clientHeight: h } = this.host;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  async load(url: string): Promise<void> {
    const gltf = await new GLTFLoader().loadAsync(url);
    this.root.clear();
    this.parts.clear();
    this.hidden.clear();
    this.root.add(gltf.scene);
    gltf.scene.updateMatrixWorld(true);
    this.box.setFromObject(gltf.scene);
    this.focus = this.detailBox(gltf.scene);
    const centre = this.focus.getCenter(new THREE.Vector3());
    const reach = this.focus.getSize(new THREE.Vector3()).length();
    const edge = new THREE.LineBasicMaterial({ color: 0x17181a, transparent: true, opacity: 0.32, clippingPlanes: [this.slice] });
    gltf.scene.traverse((node) => {
      const mesh = node as THREE.Mesh;
      if (!mesh.isMesh) return;
      const name = this.partName(mesh);
      mesh.castShadow = mesh.receiveShadow = true;
      /* Each part has its own material, so one can light up alone. A thin layer lying on something draws just in front of it, or the two flicker where they meet. */
      const thin = new THREE.Box3().setFromObject(mesh).getSize(new THREE.Vector3()).toArray().sort((a, b) => a - b)[0] < THIN;
      const own = (m: THREE.Material) => {
        const held = m.clone() as THREE.MeshStandardMaterial;
        held.clippingPlanes = [this.slice];
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
    this.setSlice(1);
    this.setView("iso");
  }

  /* What to frame: the whole model, less anything that stands far above the rest, such as a full-height door over a sill. */
  private detailBox(model: THREE.Object3D): THREE.Box3 {
    const height = this.box.max.y - this.box.min.y;
    const low = new THREE.Box3();
    model.traverse((node) => {
      if (!(node as THREE.Mesh).isMesh) return;
      const box = new THREE.Box3().setFromObject(node);
      if (box.max.y - box.min.y < TALL * height) low.union(box);
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
      part.mesh.visible = !this.hidden.has(name);
      part.mesh.position.copy(part.home).addScaledVector(part.push, this.spread);
      this.tint(name);
    }
  }

  private tint(name: string): void {
    const part = this.parts.get(name);
    if (!part) return;
    const colour = name === this.selected ? CHOSEN : name === this.hovered ? HOVER : 0x000000;
    const strength = name === this.selected ? 0.42 : name === this.hovered ? 0.24 : 0;
    const paint = (m: THREE.MeshStandardMaterial) => { m.emissive.setHex(colour); m.emissiveIntensity = strength; };
    if (Array.isArray(part.mesh.material)) (part.mesh.material as THREE.MeshStandardMaterial[]).forEach(paint); else paint(part.mesh.material as THREE.MeshStandardMaterial);
  }

  setLook(look: Look): void {
    this.look = look;
    for (const part of this.parts.values()) {
      if (look === "clay") {
        const clay = CLAY.clone();
        clay.clippingPlanes = [this.slice];
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

  setSpread(share: number): void { this.spread = share; this.apply(); }

  /* share 1 shows it all; lower cuts away the run, from the far end back to the section. */
  setSlice(share: number): void {
    const length = this.box.max.x - this.box.min.x;
    this.slice.constant = this.box.min.x + length * share + 1e-4;
  }

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

  setView(view: View, fit = 1.5): void {
    const shown = view === "section" ? this.box : this.focus;
    const middle = shown.getCenter(new THREE.Vector3());
    const reach = shown.getSize(new THREE.Vector3()).length() / 2;
    const away = {
      iso: new THREE.Vector3(-1.0, 0.62, 0.72),
      other: new THREE.Vector3(-1.0, 0.62, -0.72),
      section: new THREE.Vector3(-1, 0, 0),
      top: new THREE.Vector3(0, 1, 0.001),
    }[view].normalize();
    this.camera.fov = view === "section" ? FLAT_FOV : FOV;  /* a narrow lens looks flat on, so the section lines up with the drawing */
    const distance = (fit * reach) / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2)) * (view === "section" ? 0.62 : 0.72);
    this.controls.target.copy(middle);
    this.camera.position.copy(middle).addScaledVector(away, distance);
    this.camera.near = distance / 100;
    this.camera.far = distance * 12;
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }

  zoom(by: number): void {
    const toTarget = this.camera.position.clone().sub(this.controls.target).multiplyScalar(by);
    this.camera.position.copy(this.controls.target).add(toTarget);
  }

  /* Plays the build, step by step. Resolves when it finishes or is stopped. */
  async play(onStep: (step: number) => void): Promise<void> {
    const run = ++this.playing;
    const steps = [...new Set([...this.parts.values()].map((p) => p.step))].sort((a, b) => a - b);
    for (const part of this.parts.values()) part.mesh.visible = false;
    for (const step of steps) {
      if (run !== this.playing) return;
      onStep(step);
      for (const [name, part] of this.parts) if (part.step === step && !this.hidden.has(name)) part.mesh.visible = true;
      await new Promise((done) => setTimeout(done, STEP_MS));
    }
    this.apply();
    onStep(-1);
  }

  stopPlay(): void { this.playing++; this.apply(); }

  private pick(e: PointerEvent): string | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const meshes = [...this.parts.values()].map((p) => p.mesh).filter((m) => m.visible);
    const hit = this.raycaster.intersectObjects(meshes, false).find((h) => h.point.x <= this.slice.constant);
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
