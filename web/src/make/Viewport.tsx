import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { needsZUpMmConversion } from "./units";

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

export default function Viewport({ url }: { url: string | null }): JSX.Element {
  const host = useRef<HTMLDivElement>(null);
  const [note, setNote] = useState<string | null>(null);

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

    let frame = 0;
    let disposed = false;
    const loop = () => {
      if (disposed) return;
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(loop);
    };
    loop();

    let model: THREE.Object3D | null = null;
    if (url) {
      setNote("Loading model…");
      const loader = new GLTFLoader();
      loader.load(url, (gltf) => {
        if (disposed) return;
        model = gltf.scene;
        orientLoadedModel(model);
        scene.add(model);
        fit(camera, controls, model);
        const span = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3()).length();
        grid.scale.setScalar(Math.max(span, 0.4));
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
      controls.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, [url]);

  return (
    <div className="make-view" ref={host} data-make="viewport">
      {note && <p className="make-empty" data-make="viewport-note">{note}</p>}
      {!url && !note && <p className="make-empty">The scene will appear here after the first build.</p>}
    </div>
  );
}
