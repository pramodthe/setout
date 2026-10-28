/* Three.js viewport. Verbatim port of app/static/app.js 3D section:
   same orbit, picking, explode, clip, install-visibility semantics. */

import * as THREE from "three";
import type { Scene as SceneData } from "./types";

const K = 0.01; // mm -> scene units (== app/units.py MM_PER_VIEW_UNIT)

export class Viewport3D {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private root = new THREE.Group();
  private clipPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 1e9);
  private meshes = new Map<string, THREE.Mesh>();
  private baseY = new Map<string, number>();
  private ray = new THREE.Raycaster();
  private ndc = new THREE.Vector2();
  private yaw = -0.7;
  private pitch = 0.32;
  private dist = 6;
  private radius = 6;
  private drag = false;
  private px = 0;
  private py = 0;
  private raf = 0;
  private disposed = false;
  private container: HTMLElement;
  private onPick: (id: string) => void;
  private order: string[] = [];
  private visible: Record<string, boolean> = {};
  private seqOn = false;
  private seqIdx = -1;

  constructor(container: HTMLElement, onPick: (id: string) => void) {
    this.container = container;
    this.onPick = onPick;
    this.scene.background = new THREE.Color(0x0e1116);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x27303d, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 0.7);
    key.position.set(6, 10, 8);
    this.scene.add(key);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 500);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.localClippingEnabled = true;
    container.insertBefore(this.renderer.domElement, container.firstChild);
    this.scene.add(this.root);

    const cv = this.renderer.domElement;
    cv.addEventListener("pointerdown", this.onDown);
    window.addEventListener("pointerup", this.onUp);
    window.addEventListener("pointermove", this.onMove);
    cv.addEventListener("wheel", this.onWheel, { passive: false });
    cv.addEventListener("click", this.onClick);
    window.addEventListener("resize", this.onResize);
    this.onResize();
    this.place();
    const loop = () => {
      if (this.disposed) return;
      this.renderer.render(this.scene, this.camera);
      this.raf = requestAnimationFrame(loop);
    };
    loop();
  }

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    const cv = this.renderer.domElement;
    cv.removeEventListener("pointerdown", this.onDown);
    window.removeEventListener("pointerup", this.onUp);
    window.removeEventListener("pointermove", this.onMove);
    cv.removeEventListener("wheel", this.onWheel);
    cv.removeEventListener("click", this.onClick);
    window.removeEventListener("resize", this.onResize);
    this.meshes.forEach((m) => m.geometry.dispose());
    this.renderer.dispose();
    cv.remove();
  }

  private onDown = (e: PointerEvent): void => {
    this.drag = true;
    this.px = e.clientX;
    this.py = e.clientY;
  };
  private onUp = (): void => {
    this.drag = false;
  };
  private onMove = (e: PointerEvent): void => {
    if (!this.drag) return;
    this.yaw -= (e.clientX - this.px) * 0.007;
    this.pitch = Math.max(-1.3, Math.min(1.3, this.pitch + (e.clientY - this.py) * 0.007));
    this.px = e.clientX;
    this.py = e.clientY;
    this.place();
  };
  private onWheel = (e: WheelEvent): void => {
    e.preventDefault();
    this.dist = Math.max(
      this.radius * 0.3,
      Math.min(this.radius * 4, this.dist * (1 + Math.sign(e.deltaY) * 0.12)),
    );
    this.place();
  };
  private onClick = (e: MouseEvent): void => {
    const cv = this.renderer.domElement;
    const r = cv.getBoundingClientRect();
    this.ndc.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    this.ndc.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    this.ray.setFromCamera(this.ndc, this.camera);
    const hit = this.ray.intersectObjects(
      [...this.meshes.values()].filter((m) => m.visible),
    )[0];
    if (hit) this.onPick((hit.object.userData as { id: string }).id);
  };
  private onResize = (): void => {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  };

  private place(): void {
    this.camera.position.set(
      this.dist * Math.cos(this.pitch) * Math.sin(this.yaw),
      this.dist * Math.sin(this.pitch),
      this.dist * Math.cos(this.pitch) * Math.cos(this.yaw),
    );
    this.camera.lookAt(0, 0, 0);
  }

  loadScene(s: SceneData): void {
    this.meshes.forEach((m) => {
      this.root.remove(m);
      m.geometry.dispose();
    });
    this.meshes.clear();
    this.baseY.clear();
    this.order = s.install_order;
    for (const solid of s.solids) {
      if (!solid.tris || !solid.tris.length) continue;
      const g = new THREE.BufferGeometry();
      const pos = new Float32Array(solid.tris.length);
      for (let i = 0; i < solid.tris.length; i++) pos[i] = solid.tris[i] * K;
      g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      g.computeVertexNormals();
      const idx = this.order.indexOf(solid.id);
      const mesh = new THREE.Mesh(
        g,
        new THREE.MeshStandardMaterial({
          color: new THREE.Color(solid.color),
          roughness: 0.85,
          metalness: 0.05,
          clippingPlanes: [this.clipPlane],
          polygonOffset: true,
          polygonOffsetFactor: -idx,
          polygonOffsetUnits: -idx,
        }),
      );
      mesh.userData = { id: solid.id };
      this.meshes.set(solid.id, mesh);
      this.root.add(mesh);
    }
    const box = new THREE.Box3().setFromObject(this.root);
    const ctr = box.getCenter(new THREE.Vector3());
    this.root.position.sub(ctr);
    this.meshes.forEach((m, id) => this.baseY.set(id, m.position.y));
    const size = box.getSize(new THREE.Vector3());
    this.radius = Math.max(size.x, size.y, size.z);
    this.dist = this.radius * 1.7;
    this.applyAll();
    this.place();
  }

  setSelected(id: string | null): void {
    this.meshes.forEach((mesh, cid) => {
      const m = mesh.material as THREE.MeshStandardMaterial;
      m.emissive = new THREE.Color(cid === id ? 0x333333 : 0x000000);
    });
  }

  private applyAll(): void {
    this.applyVisibility();
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
    this.applyVisibility();
    const f = v.explode * K;
    this.order.forEach((id, i) => {
      const m = this.meshes.get(id);
      if (m) m.position.y = (this.baseY.get(id) || 0) + ((i * f) / 8);
    });
    if (v.clipOn) {
      this.clipPlane.constant = v.clipZ * K + this.root.position.z;
    } else {
      this.clipPlane.constant = 1e9;
    }
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
}
