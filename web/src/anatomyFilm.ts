import type { SceneStatus } from "./anatomyScene";

type FilmManifest = { frames: number; width: number; height: number; components: number; revision: string };
type Shot = { first: number; second: number; blend: number };
const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
function sequence(from: number, to: number, progress: number): Shot {
  const frame = from + (to - from) * clamp(progress, 0, 1);
  return { first: Math.floor(frame), second: Math.ceil(frame), blend: frame % 1 };
}

// Deliberate dissolves between photographic setups; the floor lift and the whole
// building explosion are continuous sequences rendered from the source model.
function shotAt(p: number): Shot {
  if (p < 0.82) return sequence(0, 24, p / 0.82);
  if (p < 1) return { first: 24, second: 25, blend: (p - 0.82) / 0.18 };
  if (p < 2.65) return sequence(25, 57, (p - 1) / 1.65);
  if (p < 2.95) return { first: 57, second: 58, blend: (p - 2.65) / 0.3 };
  if (p < 3.65) return sequence(58, 82, (p - 2.95) / 0.7);
  if (p < 4) return { first: 82, second: 83, blend: (p - 3.65) / 0.35 };
  if (p <= 5) return sequence(83, 115, p - 4);
  if (p < 5.8) return sequence(115, 83, (p - 5) / 0.8);
  return { first: 83, second: 0, blend: clamp((p - 5.8) / 0.2, 0, 1) };
}

export class AnatomyFilm {
  private canvas = document.createElement("canvas");
  private context: CanvasRenderingContext2D;
  private observer: ResizeObserver;
  private controller = new AbortController();
  private cache = new Map<number, ImageBitmap>();
  private pending = new Set<number>();
  private queue: number[] = [];
  private running = 0;
  private disposed = false;
  private manifest?: FilmManifest;
  private progress = 0;
  private shown = 0;
  private reduced = false;
  private active = true;
  private needsDraw = true;
  private frame = 0;
  private lastTime = 0;
  private lastFirst = -1;
  private ready = false;
  private lastImage?: ImageBitmap;
  private host: HTMLElement;
  private onStatus: (status: SceneStatus) => void;
  private base = `${import.meta.env.BASE_URL}assets/anatomy/film-fog/`;
  private backdrop = "#c7c7c7";
  // The opening orbit, 0 to 1 across frames 0 to 24: the pointer steers it,
  // and it drifts back and forth on its own once the pointer rests.
  private orbit = 0;
  private drift = 1;
  private aim: number | null = null;
  private aimedAt = 0;

  constructor(host: HTMLElement, onStatus: (status: SceneStatus) => void) {
    this.host = host;
    this.onStatus = onStatus;
    const context = this.canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("This browser could not start the rendered view.");
    this.context = context;
    this.canvas.setAttribute("role", "img");
    this.canvas.setAttribute("aria-label", "Photographic Blender render of the house, its floor structure, and the hidden steel connection");
    host.append(this.canvas);
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(host);
    this.resize();
    this.frame = requestAnimationFrame(this.render);
    void this.load();
  }

  private async load() {
    this.onStatus({ loading: 10, ready: false });
    try {
      const response = await fetch(`${this.base}manifest.json`, { signal: this.controller.signal });
      if (!response.ok) throw new Error("The rendered sequence is unavailable. Please retry.");
      this.manifest = await response.json();
      if (this.disposed) return;
      this.onStatus({ loading: 45, ready: false });
      const initial = shotAt(this.reduced ? Math.round(this.progress) : this.progress);
      this.request([initial.first, initial.second, initial.first + 1, initial.first - 1]);
    } catch (error) {
      if (!this.disposed) this.onStatus({ loading: 0, ready: false, error: error instanceof Error ? error.message : "Could not load the film." });
    }
  }

  private request(indices: number[]) {
    if (!this.manifest) return;
    const wanted = [...new Set(indices.map((n) => clamp(n, 0, this.manifest!.frames - 1)))];
    this.queue = [...wanted.filter((n) => !this.cache.has(n) && !this.pending.has(n)), ...this.queue.filter((n) => !wanted.includes(n))].slice(0, 18);
    this.pump();
  }

  private pump() {
    while (this.running < 3 && this.queue.length && !this.disposed) {
      const index = this.queue.shift()!;
      if (this.cache.has(index) || this.pending.has(index)) continue;
      this.running++;
      this.pending.add(index);
      void this.loadFrame(index);
    }
  }

  private async loadFrame(index: number) {
    try {
      const response = await fetch(`${this.base}${String(index).padStart(3, "0")}.webp?v=${this.manifest!.revision}`, { signal: this.controller.signal });
      if (!response.ok) throw new Error("A rendered frame could not load. Please retry.");
      const bitmap = await createImageBitmap(await response.blob(), innerWidth <= 700 ? { resizeWidth: 960, resizeQuality: "high" } : {});
      if (this.disposed) { bitmap.close(); return; }
      this.cache.set(index, bitmap);
      if (!this.ready) {
        this.ready = true;
        this.sampleBackdrop(bitmap);
        this.onStatus({ loading: 100, ready: true, components: this.manifest!.components });
      }
      this.needsDraw = true;
      while (this.cache.size > 18) {
        const oldest = this.cache.keys().next().value!;
        const image = this.cache.get(oldest)!;
        if (image === this.lastImage) {
          this.cache.delete(oldest); this.cache.set(oldest, image);
          continue;
        }
        image.close(); this.cache.delete(oldest);
      }
    } catch (error) {
      if (!this.disposed && !this.ready) this.onStatus({ loading: 0, ready: false, error: error instanceof Error ? error.message : "Could not load the film." });
    } finally {
      this.pending.delete(index);
      this.running--;
      this.pump();
    }
  }

  setProgress(progress: number, reducedMotion: boolean) {
    this.progress = clamp(progress, 0, 6);
    this.reduced = reducedMotion;
    if (!this.ready) this.shown = this.reduced ? Math.round(this.progress) : this.progress;
    this.needsDraw = true;
  }

  /** Pointer position across the window, -1 to 1, or null when it leaves. */
  setPointer(x: number | null) {
    this.aim = x === null ? null : clamp((x + 1) / 2, 0, 1);
    this.aimedAt = performance.now();
  }

  // The page takes the render's own backdrop, so the frame has no visible edge.
  private sampleBackdrop(image: ImageBitmap) {
    const probe = document.createElement("canvas");
    probe.width = probe.height = 1;
    const context = probe.getContext("2d");
    if (!context) return;
    context.drawImage(image, 2, 2, 1, 1, 0, 0, 1, 1);
    const [r, g, b] = context.getImageData(0, 0, 1, 1).data;
    this.backdrop = `rgb(${r} ${g} ${b})`;
    this.onBackdrop?.(this.backdrop);
  }

  onBackdrop?: (color: string) => void;

  setActive(active: boolean) {
    this.active = active;
    this.needsDraw = true;
    if (!active) {
      this.queue = [];
      for (const [index, image] of this.cache) {
        if (image !== this.lastImage) { image.close(); this.cache.delete(index); }
      }
    }
  }

  private resize() {
    const ratio = Math.min(devicePixelRatio, 1.7);
    this.canvas.width = Math.round(this.host.clientWidth * ratio);
    this.canvas.height = Math.round(this.host.clientHeight * ratio);
    this.needsDraw = true;
  }

  private draw(image: ImageBitmap, opacity: number) {
    const { width, height } = this.canvas;
    const mobile = innerWidth <= 700;
    const p = this.shown;
    let crop = 0.37;
    if (p >= 0.82 && p < 2.65) crop -= 0.17 * clamp((p - 0.82) / 0.18, 0, 1);
    else if (p >= 2.65 && p < 2.95) crop = 0.2 + 0.17 * (p - 2.65) / 0.3;
    else if (p >= 4) crop -= 0.17 * (p <= 5 ? p - 4 : 6 - p);
    const sourceX = mobile ? image.width * crop : 0;
    const sourceWidth = image.width - sourceX;
    const scale = Math.min(width / sourceWidth, height / image.height);
    const drawWidth = sourceWidth * scale;
    const drawHeight = image.height * scale;
    const x = (width - drawWidth) / 2;
    const y = (height - drawHeight) / 2;
    this.context.globalAlpha = opacity;
    // Extend the empty studio backdrop into letterbox space without cropping
    // the building or stretching any construction geometry.
    if (x > 0) {
      this.context.drawImage(image, sourceX, 0, 1, image.height, 0, y, x, drawHeight);
      this.context.drawImage(image, image.width - 1, 0, 1, image.height, x + drawWidth, y, x, drawHeight);
    }
    if (y > 0) {
      this.context.drawImage(image, sourceX, 0, sourceWidth, 1, x, 0, drawWidth, y);
      this.context.drawImage(image, sourceX, image.height - 1, sourceWidth, 1, x, y + drawHeight, drawWidth, y);
    }
    this.context.drawImage(image, sourceX, 0, sourceWidth, image.height, x, y, drawWidth, drawHeight);
  }

  private render = (time: number) => {
    if (this.disposed) return;
    this.frame = requestAnimationFrame(this.render);
    const dt = Math.min((time - this.lastTime) / 1000, 0.05);
    this.lastTime = time;
    if (!this.active || document.hidden || !this.manifest) return;
    const target = this.reduced ? Math.round(this.progress) : this.progress;
    const moving = Math.abs(target - this.shown) > 0.0003;
    this.shown = this.reduced ? target : this.shown + (target - this.shown) * (1 - Math.exp(-dt * 10));
    let shot = shotAt(this.shown);
    const playing = this.progress < 0.015 && !this.reduced;
    if (playing) {
      // A real camera orbit in the opening, not a flat image pan.
      if (this.aim !== null && time - this.aimedAt < 2500) {
        this.orbit += (this.aim - this.orbit) * (1 - Math.exp(-dt * 4));
      } else {
        this.orbit += this.drift * dt / 6;
        if (this.orbit > 1 || this.orbit < 0) { this.drift = -this.drift; this.orbit = clamp(this.orbit, 0, 1); }
      }
    }
    // Scrolling carries the camera on from wherever the orbit rests, so the
    // first move never jumps back to frame 0.
    if (!this.reduced && this.shown < 0.82) shot = sequence(0, 24, this.orbit + (1 - this.orbit) * clamp(this.shown / 0.82, 0, 1));
    if (shot.first !== this.lastFirst || this.needsDraw) {
      this.request([shot.first, ...(shot.blend > 0 ? [shot.second] : []), shot.first + 1, shot.first - 1, shot.first + 2, shot.first - 2, shot.first + 3]);
      this.lastFirst = shot.first;
    }
    if (!this.needsDraw && !moving && !playing && this.cache.get(shot.first) === this.lastImage) return;
    const first = this.cache.get(shot.first);
    const image = first || this.lastImage || this.cache.values().next().value;
    if (!image) return;
    if (first) {
      this.cache.delete(shot.first); this.cache.set(shot.first, first);
    }
    this.lastImage = image;
    this.context.globalAlpha = 1;
    this.context.fillStyle = this.backdrop;
    this.context.fillRect(0, 0, this.canvas.width, this.canvas.height);
    this.draw(image, 1);
    const second = this.cache.get(shot.second);
    if (shot.blend > 0 && second) this.draw(second, shot.blend);
    this.needsDraw = false;
  };

  dispose() {
    this.disposed = true;
    this.controller.abort();
    cancelAnimationFrame(this.frame);
    this.observer.disconnect();
    this.cache.forEach((image) => image.close());
    this.cache.clear();
    this.canvas.remove();
  }
}
