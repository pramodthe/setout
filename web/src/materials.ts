/* Procedural material library. Every texture is drawn by this code at
   runtime (no third-party image assets): tileable value noise plus simple
   pattern passes per material class. Sizes are real-world (repeatMm), so
   with box-projected UVs (uv.ts) tiles, trowel ridges and grain are to scale. */

import * as THREE from "three";

export type MaterialClass =
  | "concrete" | "screed" | "primer" | "membrane" | "sheet" | "tape"
  | "adhesive" | "tile" | "grout" | "sealant" | "foam" | "plastic"
  | "aluminium" | "steel" | "timber" | "fibre_cement" | "insulation"
  | "gravel" | "fabric" | "ceramic" | "glass" | "generic";

/* Ordered: first match wins (e.g. backing_rod is foam, not steel rod;
   cfc_packers are fibre cement, not plastic packers). */
const RULES: [RegExp, MaterialClass][] = [
  [/screen|glass|glazing/, "glass"],
  [/bath|basin|wc|toilet|pan\b|tray|sanitary|ceramic|cistern/, "ceramic"],
  [/grate|mixer|(^|_)tap(_|$)|handle|bracket|spindle/, "steel"],
  [/primer|slurry/, "primer"],
  [/backing_rod|perimeter_joint|compressible|foam/, "foam"],
  [/cfc|fibre_cement|fiber_cement|plasterboard|cement_sheet/, "fibre_cement"],
  [/membrane|waterproof|wpm|nut_patch/, "membrane"],
  [/tape|fleece/, "tape"],
  [/adhesive/, "adhesive"],
  [/grout/, "grout"],
  [/tile|stone|terracotta|terrazzo|paver/, "tile"],
  [/sealant|silicone|fillet|bead|mastic/, "sealant"],
  [/screed|leveller|mortar|render|bedding/, "screed"],
  [/slab|substrate|concrete|masonry|block|footing|wall|hob|kerb|nib|plinth/, "concrete"],
  [/protection|slip|sheet|bond_breaker/, "sheet"],
  [/frame|sill|angle|waterstop|flashing|alumin|trim/, "aluminium"],
  [/rod|nut|washer|bolt|screw|anchor|fixing|steel|rebar/, "steel"],
  [/packer|shim|plastic|drainage_cell|weep|drain/, "plastic"],
  [/timber|joist|ply|osb|particle|door|vanity|cabinet/, "timber"],
  [/insulation/, "insulation"],
  [/gravel|backfill|aggregate/, "gravel"],
  [/geofabric|geotextile|fabric/, "fabric"],
];

export function materialClassFor(type: string, name = ""): MaterialClass {
  const s = `${type} ${name}`.toLowerCase();
  for (const [re, cls] of RULES) if (re.test(s)) return cls;
  return "generic";
}

interface Spec {
  base: string;
  roughness: number;
  metalness: number;
  repeatMm: number; // real-world size one texture tile covers
  opacity?: number; // < 1 for see-through materials (glass)
  bump: number; // bumpScale; 0 = no relief
  paint?: (px: Uint8ClampedArray, n: number, rand: () => number) => void;
  noise: number; // albedo noise amplitude 0..1
  octaves: number[]; // lattice cells per octave
}

export const SPECS: Record<MaterialClass, Spec> = {
  concrete: { base: "#8f8d87", roughness: 0.92, metalness: 0, repeatMm: 500, bump: 0.6,
    noise: 0.28, octaves: [4, 8, 16, 32, 64, 128], paint: speckle(0.012, 38) },
  screed: { base: "#6c6964", roughness: 0.9, metalness: 0, repeatMm: 400, bump: 0.5,
    noise: 0.2, octaves: [8, 32, 128], paint: speckle(0.05, 26) },
  primer: { base: "#5e927a", roughness: 0.6, metalness: 0, repeatMm: 300, bump: 0.15,
    noise: 0.12, octaves: [4, 16, 64], paint: streaks(0.06) },
  membrane: { base: "#86b5df", roughness: 0.42, metalness: 0, repeatMm: 250, bump: 0.25,
    noise: 0.12, octaves: [16, 64, 256] },
  sheet: { base: "#e7eae7", roughness: 0.5, metalness: 0, repeatMm: 300, bump: 0.05,
    noise: 0.05, octaves: [8, 32] },
  tape: { base: "#f2f1ea", roughness: 0.85, metalness: 0, repeatMm: 120, bump: 0.2,
    noise: 0.06, octaves: [32, 128], paint: fibres(900) },
  adhesive: { base: "#d9d4ca", roughness: 0.9, metalness: 0, repeatMm: 60, bump: 1.2,
    noise: 0.08, octaves: [16, 64], paint: ridges(10) },
  tile: { base: "#cdc3b3", roughness: 0.32, metalness: 0, repeatMm: 600, bump: 0.8,
    noise: 0.07, octaves: [4, 16], paint: tileGrid(2, 3 / 600, "#8d877d") },
  grout: { base: "#8d877d", roughness: 0.85, metalness: 0, repeatMm: 100, bump: 0.3,
    noise: 0.15, octaves: [16, 64, 128] },
  sealant: { base: "#4c5158", roughness: 0.25, metalness: 0, repeatMm: 100, bump: 0.05,
    noise: 0.04, octaves: [8] },
  foam: { base: "#4d6f9e", roughness: 0.95, metalness: 0, repeatMm: 60, bump: 0.4,
    noise: 0.18, octaves: [32, 128] },
  plastic: { base: "#e1903b", roughness: 0.5, metalness: 0, repeatMm: 100, bump: 0.05,
    noise: 0.04, octaves: [8] },
  aluminium: { base: "#c4c9ce", roughness: 0.33, metalness: 0.85, repeatMm: 200, bump: 0.1,
    noise: 0.1, octaves: [4], paint: brushed(0.08) },
  steel: { base: "#8d939a", roughness: 0.4, metalness: 0.8, repeatMm: 100, bump: 0.1,
    noise: 0.08, octaves: [4], paint: brushed(0.1) },
  timber: { base: "#b98a5c", roughness: 0.75, metalness: 0, repeatMm: 400, bump: 0.4,
    noise: 0.1, octaves: [4, 16], paint: grain(14) },
  fibre_cement: { base: "#cfc9bd", roughness: 0.85, metalness: 0, repeatMm: 300, bump: 0.2,
    noise: 0.08, octaves: [16, 64, 256], paint: speckle(0.02, 18) },
  insulation: { base: "#e6d16b", roughness: 1, metalness: 0, repeatMm: 200, bump: 0.6,
    noise: 0.22, octaves: [16, 64], paint: fibres(1400) },
  gravel: { base: "#8c8070", roughness: 0.95, metalness: 0, repeatMm: 200, bump: 1,
    noise: 0.1, octaves: [8], paint: pebbles(260) },
  fabric: { base: "#d9d6cf", roughness: 0.95, metalness: 0, repeatMm: 50, bump: 0.3,
    noise: 0.06, octaves: [16], paint: weave(24) },
  ceramic: { base: "#f3f2ee", roughness: 0.12, metalness: 0, repeatMm: 400, bump: 0,
    noise: 0.02, octaves: [8] },
  glass: { base: "#cfe3ea", roughness: 0.05, metalness: 0, repeatMm: 400, bump: 0,
    noise: 0, octaves: [], opacity: 0.32 },
  generic: { base: "#9aa3ad", roughness: 0.7, metalness: 0, repeatMm: 200, bump: 0,
    noise: 0, octaves: [] },
};

const SIZE = 512;

function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const smooth = (t: number) => t * t * (3 - 2 * t);

/* Tileable value noise in 0..1 (lattice wraps, so RepeatWrapping is seamless). */
export function tileNoise(n: number, octaves: number[], rand: () => number): Float32Array {
  const out = new Float32Array(n * n);
  if (!octaves.length) return out.fill(0.5);
  let wsum = 0;
  octaves.forEach((cells, oi) => {
    const w = 1 / (oi + 1);
    wsum += w;
    const lat = new Float32Array(cells * cells);
    for (let i = 0; i < lat.length; i++) lat[i] = rand();
    for (let y = 0; y < n; y++) {
      const fy = (y / n) * cells;
      const y0 = Math.floor(fy);
      const ty = smooth(fy - y0);
      const r0 = y0 * cells;
      const r1 = ((y0 + 1) % cells) * cells;
      for (let x = 0; x < n; x++) {
        const fx = (x / n) * cells;
        const x0 = Math.floor(fx);
        const tx = smooth(fx - x0);
        const x1 = (x0 + 1) % cells;
        const top = lat[r0 + x0] + (lat[r0 + x1] - lat[r0 + x0]) * tx;
        const bot = lat[r1 + x0] + (lat[r1 + x1] - lat[r1 + x0]) * tx;
        out[y * n + x] += w * (top + (bot - top) * ty);
      }
    }
  });
  for (let i = 0; i < out.length; i++) out[i] /= wsum;
  return out;
}

function rgb(hex: string): [number, number, number] {
  const c = new THREE.Color(hex);
  return [c.r * 255, c.g * 255, c.b * 255];
}

function shade(px: Uint8ClampedArray, i: number, f: number): void {
  px[i] *= f;
  px[i + 1] *= f;
  px[i + 2] *= f;
}

/* ---- pattern passes (operate on RGBA pixels, SIZE x SIZE) ---- */

function speckle(density: number, depth: number) {
  return (px: Uint8ClampedArray, n: number, rand: () => number) => {
    const count = Math.floor(n * n * density);
    for (let k = 0; k < count; k++) {
      const i = (Math.floor(rand() * n) * n + Math.floor(rand() * n)) * 4;
      const d = (rand() - 0.6) * depth;
      px[i] += d;
      px[i + 1] += d;
      px[i + 2] += d;
    }
  };
}

function streaks(amp: number) {
  return (px: Uint8ClampedArray, n: number, rand: () => number) => {
    const phase = Array.from({ length: 6 }, () => rand() * Math.PI * 2);
    for (let y = 0; y < n; y++) {
      const s = 1 + amp * Math.sin((y / n) * Math.PI * 2 * 9 + phase[y % 6] * 0.15);
      for (let x = 0; x < n; x++) shade(px, (y * n + x) * 4, s);
    }
  };
}

function ridges(count: number) {
  // notched-trowel combing: count ridges per tile, valleys darker
  return (px: Uint8ClampedArray, n: number) => {
    for (let y = 0; y < n; y++) {
      const t = ((y / n) * count) % 1;
      const s = 0.72 + 0.36 * Math.sin(t * Math.PI);
      for (let x = 0; x < n; x++) shade(px, (y * n + x) * 4, s);
    }
  };
}

function tileGrid(perSide: number, groutFrac: number, grout: string) {
  // perSide tiles across the texture; grout lines groutFrac of the texture wide
  const [gr, gg, gb] = rgb(grout);
  return (px: Uint8ClampedArray, n: number, rand: () => number) => {
    const cell = n / perSide;
    const half = Math.max(1, (groutFrac * n) / 2);
    const tint = Array.from({ length: perSide * perSide }, () => 0.94 + rand() * 0.1);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = (y * n + x) * 4;
        const dx = Math.min(x % cell, cell - (x % cell));
        const dy = Math.min(y % cell, cell - (y % cell));
        if (dx < half || dy < half) {
          px[i] = gr;
          px[i + 1] = gg;
          px[i + 2] = gb;
        } else {
          shade(px, i, tint[Math.floor(y / cell) * perSide + Math.floor(x / cell)]);
        }
      }
    }
  };
}

function brushed(amp: number) {
  return (px: Uint8ClampedArray, n: number, rand: () => number) => {
    const row = Array.from({ length: n }, () => 1 + (rand() - 0.5) * amp);
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) shade(px, (y * n + x) * 4, row[y]);
  };
}

function grain(rings: number) {
  return (px: Uint8ClampedArray, n: number, rand: () => number) => {
    const warp = tileNoise(n, [4, 8], rand);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const v = Math.sin(((y / n) * rings + warp[y * n + x] * 2.2) * Math.PI * 2);
        shade(px, (y * n + x) * 4, 0.88 + 0.12 * v);
      }
    }
  };
}

function fibres(count: number) {
  return (px: Uint8ClampedArray, n: number, rand: () => number) => {
    for (let k = 0; k < count; k++) {
      let x = rand() * n;
      let y = rand() * n;
      const a = rand() * Math.PI * 2;
      const len = 6 + rand() * 18;
      const d = 0.9 + rand() * 0.15;
      for (let s = 0; s < len; s++) {
        x = (x + Math.cos(a) + n) % n;
        y = (y + Math.sin(a) + n) % n;
        shade(px, (Math.floor(y) * n + Math.floor(x)) * 4, d);
      }
    }
  };
}

function pebbles(count: number) {
  return (px: Uint8ClampedArray, n: number, rand: () => number) => {
    for (let k = 0; k < count; k++) {
      const cx = rand() * n;
      const cy = rand() * n;
      const r = 5 + rand() * 12;
      const f = 0.75 + rand() * 0.5;
      for (let y = -r; y <= r; y++) {
        for (let x = -r; x <= r; x++) {
          if (x * x + y * y > r * r) continue;
          const px_ = Math.floor((cx + x + n) % n);
          const py_ = Math.floor((cy + y + n) % n);
          shade(px, (py_ * n + px_) * 4, f * (1 - (0.25 * (x * x + y * y)) / (r * r)));
        }
      }
    }
  };
}

function weave(threads: number) {
  return (px: Uint8ClampedArray, n: number) => {
    const p = n / threads;
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const over = (Math.floor(x / p) + Math.floor(y / p)) % 2 === 0;
        const t = over ? (x % p) / p : (y % p) / p;
        shade(px, (y * n + x) * 4, 0.86 + 0.14 * Math.sin(t * Math.PI));
      }
    }
  };
}

/* ---- textures + materials ---- */

const cache = new Map<MaterialClass, THREE.CanvasTexture>();

function texture(cls: MaterialClass): THREE.CanvasTexture | null {
  const spec = SPECS[cls];
  if (cls === "generic") return null;
  const hit = cache.get(cls);
  if (hit) return hit;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = SIZE;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const img = ctx.createImageData(SIZE, SIZE);
  const px = img.data;
  const rand = rng(cls.length * 7919 + cls.charCodeAt(0));
  const noise = tileNoise(SIZE, spec.octaves, rand);
  const [r, g, b] = rgb(spec.base);
  for (let i = 0; i < SIZE * SIZE; i++) {
    const f = 1 + spec.noise * (noise[i] - 0.5) * 2;
    px[i * 4] = r * f;
    px[i * 4 + 1] = g * f;
    px[i * 4 + 2] = b * f;
    px[i * 4 + 3] = 255;
  }
  spec.paint?.(px, SIZE, rand);
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.encoding = THREE.sRGBEncoding;
  tex.anisotropy = 8;
  cache.set(cls, tex);
  return tex;
}

export function makeMaterial(cls: MaterialClass, fallbackColor: string): THREE.MeshStandardMaterial {
  const spec = SPECS[cls];
  const map = texture(cls);
  const flat = cls === "glass" ? new THREE.Color(spec.base) : new THREE.Color(fallbackColor);
  return new THREE.MeshStandardMaterial({
    color: map ? 0xffffff : flat,
    map,
    bumpMap: map && spec.bump ? map : null,
    bumpScale: spec.bump * 0.02,
    roughness: spec.roughness,
    metalness: spec.metalness,
  });
}

export function disposeTextures(): void {
  cache.forEach((t) => t.dispose());
  cache.clear();
}
