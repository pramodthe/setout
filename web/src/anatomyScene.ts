import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";

type Attribute = { offset: number; length: number; type: string; itemSize: number };
type SourceMaterial = { color: number[]; roughness: number; metalness: number; texture: string | null };
type AnatomyModel = { version: number; components: number; attributes: Record<string, Attribute>; materials: SourceMaterial[]; groups: { start: number; count: number; material: number }[] };
export type SceneStatus = { loading: number; ready: boolean; error?: string; components?: number };

// The film owns scroll playback; the live viewer only needs its two detail views.
const detailViews = {
  floor: { yaw: 2.35, pitch: 0.48, target: [-1.4, -0.5, -0.55] },
  hanger: { yaw: 2.25, pitch: 0.23, target: [-3.285, -0.832, 0.45] },
};

export class AnatomyScene {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
  private controls: OrbitControls;
  private mesh?: THREE.Mesh;
  private materials: THREE.MeshStandardMaterial[] = [];
  private textures: THREE.Texture[] = [];
  private environment: THREE.WebGLRenderTarget;
  private uniforms = { uSpread: { value: 0 }, uIsolate: { value: -1 }, uStudy: { value: 0 }, uLift: { value: 0 }, uJoint: { value: 0 } };
  private observer: ResizeObserver;
  private abort = new AbortController();
  private frame = 0;
  private disposed = false;
  private manualSpread = 1;
  private needsRender = true;
  private host: HTMLElement;
  private onStatus: (s: SceneStatus) => void;

  constructor(host: HTMLElement, onStatus: (s: SceneStatus) => void) {
    this.host = host;
    this.onStatus = onStatus;
    this.renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, innerWidth < 700 ? 1.5 : 2));
    this.renderer.outputEncoding = THREE.sRGBEncoding;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.95;
    this.renderer.setClearColor(0x151714, 0);
    this.renderer.domElement.setAttribute("aria-label", "Textured architectural assembly, revealing the floor joists and their steel connection");
    this.renderer.domElement.setAttribute("role", "img");
    host.append(this.renderer.domElement);
    // A studio environment gives metal a real reflection and avoids shadow-map
    // acne on the source's millimetre-thick, overlapping construction layers.
    const generator = new THREE.PMREMGenerator(this.renderer);
    const room = new RoomEnvironment();
    this.environment = generator.fromScene(room, 0.04);
    this.scene.environment = this.environment.texture;
    room.dispose();
    generator.dispose();
    this.scene.add(new THREE.HemisphereLight(0xfff5e5, 0x656c68, 0.4));
    const key = new THREE.DirectionalLight(0xffefd5, 1.05);
    key.position.set(-6, 14, -8);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xe4efff, 0.55);
    fill.position.set(8, 5, 12);
    this.scene.add(fill);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.controls.minDistance = 0.35;
    this.controls.maxDistance = 65;
    this.controls.addEventListener("change", () => { this.needsRender = true; });
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(host);
    this.resize();
    this.resetView();
    this.render();
    void this.load();
  }

  private makeMaterial(source: SourceMaterial, map: THREE.Texture | null) {
    const material = new THREE.MeshStandardMaterial({
      color: new THREE.Color().fromArray(source.color), map,
      roughness: source.roughness, metalness: source.metalness,
      envMapIntensity: 0.55, side: THREE.FrontSide,
    });
    material.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, this.uniforms);
      shader.vertexShader = `attribute vec3 explode;
attribute float layer;
attribute float feature;
uniform float uSpread;
uniform float uLift;
varying float vLayer;
varying float vFeature;
varying vec3 vSourcePosition;
${shader.vertexShader}`.replace("#include <begin_vertex>", `
        vLayer = layer;
        vFeature = feature;
        vSourcePosition = position;
        vec3 transformed = position + explode * uSpread;
        if (feature == 4.0) transformed.y += uLift;
      `);
      shader.fragmentShader = `uniform float uIsolate;
uniform float uStudy;
uniform float uJoint;
varying float vLayer;
varying float vFeature;
varying vec3 vSourcePosition;
${shader.fragmentShader}`.replace("#include <color_fragment>", `
        #include <color_fragment>
        if (uIsolate > -0.5 && abs(vLayer - uIsolate) > 0.1) discard;
        if (uStudy > 0.5 && vFeature < 0.5) discard;
        if (uJoint > 0.5) {
          if (vFeature < 0.5 || vFeature > 3.5) discard;
          vec3 local = vSourcePosition - vec3(-3.285, -0.832, 0.45);
          if (abs(local.x) > 0.48 || local.z < -0.48 || local.z > 0.2) discard;
        }
      `);
    };
    return material;
  }

  private async load() {
    try {
      this.onStatus({ loading: 5, ready: false });
      const base = `${import.meta.env.BASE_URL}assets/anatomy/`;
      const [metaResponse, geometryResponse] = await Promise.all([
        fetch(`${base}house.json`, { signal: this.abort.signal }),
        fetch(`${base}house.bin.gz`, { signal: this.abort.signal }),
      ]);
      if (!metaResponse.ok || !geometryResponse.ok) throw new Error("The model asset is unavailable. Please retry.");
      const metadata: AnatomyModel = await metaResponse.json();
      if (metadata.version !== 2 || !geometryResponse.body) throw new Error("The textured model needs to be re-exported.");
      const body = geometryResponse.headers.get("content-encoding")?.includes("gzip")
        ? geometryResponse.body : geometryResponse.body.pipeThrough(new DecompressionStream("gzip"));
      const buffer = await new Response(body).arrayBuffer();
      if (this.disposed) return;
      this.onStatus({ loading: 55, ready: false });
      const geometry = new THREE.BufferGeometry();
      for (const [name, attribute] of Object.entries(metadata.attributes)) {
        const { offset, length, type, itemSize } = attribute;
        const array = type === "f32" ? new Float32Array(buffer, offset, length)
          : type === "u32" ? new Uint32Array(buffer, offset, length)
            : type === "i8" ? new Int8Array(buffer, offset, length) : new Uint8Array(buffer, offset, length);
        const attr = new THREE.BufferAttribute(array, itemSize, name === "normal");
        if (name === "index") geometry.setIndex(attr);
        else geometry.setAttribute(name, attr);
      }
      const loader = new THREE.TextureLoader();
      const paths = [...new Set(metadata.materials.flatMap((m) => m.texture ? [m.texture] : []))];
      const maps = new Map<string, THREE.Texture>();
      let loaded = 0;
      const textureResults = await Promise.allSettled(paths.map(async (path) => {
        const texture = await loader.loadAsync(base + path);
        if (this.disposed) { texture.dispose(); return; }
        texture.encoding = THREE.sRGBEncoding;
        texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
        texture.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
        this.textures.push(texture);
        maps.set(path, texture);
        loaded++;
        this.onStatus({ loading: 55 + Math.round(loaded / paths.length * 40), ready: false });
      }));
      if (this.disposed) { geometry.dispose(); return; }
      if (textureResults.some((result) => result.status === "rejected")) {
        geometry.dispose();
        throw new Error("A source texture could not load. Please retry the 3D view.");
      }
      this.materials = metadata.materials.map((m) => this.makeMaterial(m, m.texture ? maps.get(m.texture) || null : null));
      for (const group of metadata.groups) geometry.addGroup(group.start, group.count, group.material);
      geometry.computeBoundingSphere();
      this.mesh = new THREE.Mesh(geometry, this.materials);
      this.mesh.frustumCulled = false;
      this.scene.add(this.mesh);
      this.needsRender = true;
      this.onStatus({ loading: 100, ready: true, components: metadata.components });
    } catch (error) {
      if (this.disposed) return;
      this.onStatus({ loading: 0, ready: false, error: error instanceof Error ? error.message : "Could not load the model." });
    }
  }

  resetView() {
    this.uniforms.uStudy.value = this.uniforms.uJoint.value = this.uniforms.uLift.value = 0;
    this.uniforms.uIsolate.value = -1;
    this.controls.target.set(0, 0, 0);
    this.camera.position.set(26, 15, 34);
    if (this.host.clientWidth / Math.max(1, this.host.clientHeight) < 0.9) this.camera.position.multiplyScalar(1.3);
    this.controls.update();
    this.needsRender = true;
  }

  setSpread(value: number) {
    if (this.uniforms.uStudy.value > 0) this.resetView();
    this.manualSpread = value;
    this.needsRender = true;
  }
  setLayer(value: number) {
    if (this.uniforms.uStudy.value > 0) this.resetView();
    this.uniforms.uIsolate.value = value;
    this.needsRender = true;
  }

  focusDetail(pattern: string, distance: number) {
    const isJoint = pattern.includes("Simpson");
    this.uniforms.uStudy.value = 1;
    this.uniforms.uJoint.value = isJoint ? 1 : 0;
    this.uniforms.uLift.value = 1.1;
    this.uniforms.uIsolate.value = -1;
    this.manualSpread = 0;
    const pose = detailViews[isJoint ? "hanger" : "floor"];
    this.controls.target.fromArray(pose.target);
    this.camera.position.copy(this.controls.target).add(new THREE.Vector3(Math.sin(pose.yaw), Math.sin(pose.pitch), Math.cos(pose.yaw)).multiplyScalar(distance * Math.max(1, 1.25 / this.camera.aspect)));
    this.controls.update();
    this.needsRender = true;
  }

  private resize() {
    const { width, height } = this.host.getBoundingClientRect();
    this.renderer.setSize(width, height);
    this.camera.aspect = width / Math.max(1, height);
    this.camera.updateProjectionMatrix();
    this.needsRender = true;
  }

  private render = () => {
    if (this.disposed) return;
    this.frame = requestAnimationFrame(this.render);
    if (document.hidden) return;
    this.uniforms.uSpread.value = this.manualSpread;
    this.controls.update();
    if (!this.needsRender) return;
    // Keep depth precision appropriate to the actual view, including close-ups.
    const distance = this.camera.position.distanceTo(this.controls.target);
    this.camera.near = Math.max(0.015, distance / 90);
    this.camera.far = Math.max(60, distance + 55);
    this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
    this.needsRender = false;
  };

  dispose() {
    this.disposed = true;
    this.abort.abort();
    cancelAnimationFrame(this.frame);
    this.observer.disconnect();
    this.controls.dispose();
    this.mesh?.geometry.dispose();
    this.materials.forEach((material) => material.dispose());
    this.textures.forEach((texture) => texture.dispose());
    this.environment.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
