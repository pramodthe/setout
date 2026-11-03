import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import "@fontsource-variable/inter-tight";
import type { AnatomyScene, SceneStatus } from "./anatomyScene";
import { AnatomyFilm } from "./anatomyFilm";
import "./anatomy.css";

// Each chapter is one screen of scrolling; the film follows the same progress.
// `place` puts the copy where that stretch of film leaves the frame empty.
type Chapter = { name: string; place: "top" | "bottom"; title: ReactNode; body?: string };
const chapters: Chapter[] = [
  { name: "The question", place: "top", title: <><span>What holds</span><span>up your</span><span>floor?</span></> },
  { name: "Lift the floor", place: "top", title: "Lift the floor", body: "Under the boards is a row of timber I-joists. What you walk on is only the top layer." },
  { name: "Follow a joist", place: "bottom", title: "Follow a joist", body: "Every joist has to end somewhere. Where it meets the beam, the load changes hands." },
  { name: "The hanger", place: "top", title: "The hanger", body: "A folded steel hanger holds the joist against the beam. Once the ceiling is closed, nobody sees it again." },
  { name: "Every part", place: "bottom", title: "Every part" },
  { name: "One detail at a time", place: "top", title: "One detail at a time", body: "Upload a section drawing or a photo of a detail. Muse, Setout’s builder, models it in Blender, checks the model against your drawing and names every part." },
  { name: "Your drawing", place: "bottom", title: "Your drawing next", body: "The house is back together, and now you know what is inside it." },
];
const layers = [{ id: -1, label: "Everything", color: "#fff" }, { id: 0, label: "Surfaces", color: "#d3cfc1" }, { id: 1, label: "Structure", color: "#b48b56" }, { id: 2, label: "Services", color: "#5f8e8d" }, { id: 4, label: "Insulation and envelope", color: "#a39c74" }, { id: 3, label: "Ground", color: "#8f7258" }];

function Arrow({ down = false }: { down?: boolean }) {
  return <span className="anatomy-arrow" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none"><path d={down ? "M8 3v10M3.5 8.5 8 13l4.5-4.5" : "M4.5 11.5 11.5 4.5M5.5 4.5h6v6"} stroke="currentColor" strokeWidth="1.3" /></svg></span>;
}

// An I-joist in section: the part this story starts from.
function Mark() {
  return <svg className="anatomy-mark" viewBox="0 0 24 28" fill="none" aria-hidden="true"><path d="M3 2.5h18v4.5h-7v14h7v4.5H3V21h7V7H3z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>;
}

export default function Anatomy() {
  const root = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const scene = useRef<AnatomyScene | null>(null);
  const filmStage = useRef<HTMLDivElement>(null);
  const film = useRef<AnatomyFilm | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const exploreTrigger = useRef<HTMLButtonElement>(null);
  const [status, setStatus] = useState<SceneStatus>({ loading: 0, ready: false });
  const [viewerStatus, setViewerStatus] = useState<SceneStatus>({ loading: 0, ready: false });
  const [exploring, setExploring] = useState(false);
  const [spread, setSpread] = useState(1);
  const [layer, setLayer] = useState(-1);
  const [retry, setRetry] = useState(0);
  const [reduced, setReduced] = useState(() => matchMedia("(prefers-reduced-motion: reduce)").matches);

  useEffect(() => {
    const title = document.title;
    document.title = "Setout: what holds up your floor?";
    document.body.classList.add("anatomy-page");
    scrollTo(0, 0);
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const change = () => setReduced(media.matches);
    media.addEventListener("change", change);
    return () => { document.title = title; document.body.classList.remove("anatomy-page"); document.body.style.removeProperty("--an-sky"); media.removeEventListener("change", change); };
  }, []);

  useEffect(() => {
    if (!filmStage.current) return;
    let active = true;
    try {
      const player = new AnatomyFilm(filmStage.current, setStatus);
      player.onBackdrop = (color) => { root.current?.style.setProperty("--an-sky", color); document.body.style.setProperty("--an-sky", color); };
      film.current = player;
    } catch { queueMicrotask(() => { if (active) setStatus({ loading: 0, ready: false, error: "This browser could not play the rendered house. The story is still here to read." }); }); }
    return () => { active = false; film.current?.dispose(); film.current = null; };
  }, [retry]);

  useEffect(() => {
    let pending = 0;
    const update = () => {
      pending = 0;
      const height = document.querySelector<HTMLElement>(".anatomy-chapter")?.offsetHeight || innerHeight;
      film.current?.setProgress(Math.min(6, Math.max(0, scrollY / height)), reduced);
    };
    const scroll = () => { if (!pending) pending = requestAnimationFrame(update); };
    // Only a mouse steers the opening orbit; touch is for scrolling.
    const point = (event: PointerEvent) => { if (event.pointerType === "mouse") film.current?.setPointer(event.clientX / innerWidth * 2 - 1); };
    const leave = () => film.current?.setPointer(null);
    update();
    addEventListener("scroll", scroll, { passive: true });
    addEventListener("resize", scroll);
    addEventListener("pointermove", point, { passive: true });
    document.documentElement.addEventListener("pointerleave", leave);
    return () => { removeEventListener("scroll", scroll); removeEventListener("resize", scroll); removeEventListener("pointermove", point); document.documentElement.removeEventListener("pointerleave", leave); cancelAnimationFrame(pending); };
  }, [reduced, retry, status.ready]);

  useEffect(() => {
    film.current?.setActive(!exploring);
    if (!exploring || !stage.current) return;
    let viewer: AnatomyScene | undefined;
    let disposed = false;
    void import("./anatomyScene").then(({ AnatomyScene: Viewer }) => {
      if (disposed || !stage.current) return;
      viewer = new Viewer(stage.current, setViewerStatus);
      scene.current = viewer;
      viewer.setSpread(1);
    }).catch(() => {
      if (!disposed) setViewerStatus({ loading: 0, ready: false, error: "The 3D model could not start on this device." });
    });
    const before = document.body.style.overflow;
    const trigger = exploreTrigger.current;
    document.body.style.overflow = "hidden";
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExploring(false);
      if (event.key === "Tab") {
        const focusable = dialog.current?.querySelectorAll<HTMLElement>("button, input");
        if (!focusable?.length) return;
        const start = focusable[0];
        const end = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === start) { event.preventDefault(); end.focus(); }
        else if (!event.shiftKey && document.activeElement === end) { event.preventDefault(); start.focus(); }
      }
    };
    addEventListener("keydown", key);
    return () => { disposed = true; viewer?.dispose(); scene.current = null; document.body.style.overflow = before; removeEventListener("keydown", key); trigger?.focus(); };
  }, [exploring]);

  function go(index: number) {
    document.getElementById(`anatomy-${index}`)?.scrollIntoView({ behavior: reduced ? "instant" : "smooth" });
  }

  function openExplore() {
    setSpread(1); setLayer(-1);
    setViewerStatus({ loading: 0, ready: false });
    setExploring(true);
  }

  function pick(id: number) { setLayer(id); scene.current?.setLayer(id); }
  function closer(pattern: string, distance: number) { setLayer(-1); setSpread(0); scene.current?.focusDetail(pattern, distance); }

  return <div ref={root} className={`anatomy${exploring ? " anatomy-exploring" : ""}${status.ready ? " anatomy-ready" : ""}`}>
    <a className="anatomy-skip" href="#anatomy-story" onClick={(e) => { e.preventDefault(); document.getElementById("anatomy-story")?.focus(); }}>Skip to the story</a>
    <header className="anatomy-header" aria-hidden={exploring || undefined}>
      <button className="anatomy-brand" onClick={() => go(0)} aria-label="Setout, back to the top"><Mark />Setout</button>
      <a className="anatomy-button" href="#/app">Open Studio <Arrow /></a>
    </header>

    <div className="anatomy-stage-wrap">
      <div ref={filmStage} className="anatomy-film-stage" />
      <div ref={stage} className="anatomy-stage anatomy-live-stage" />
      <div className="anatomy-grain" aria-hidden="true" />
      {exploring && !viewerStatus.ready && <div className="anatomy-loading" role="status"><p>{viewerStatus.error || "Loading the 3D model"}</p>{!viewerStatus.error && <div className="anatomy-load-track"><i style={{ width: `${viewerStatus.loading}%` }} /></div>}</div>}
      {!status.ready && <div className="anatomy-loading" role="status">{status.error
        ? <><p>{status.error}</p><button className="anatomy-text-button" onClick={() => { setStatus({ loading: 0, ready: false }); setRetry((n) => n + 1); }}>Try again</button></>
        : <><p>Loading the house</p><div className="anatomy-load-track"><i style={{ width: `${status.loading}%` }} /></div></>}</div>}
    </div>

    <main id="anatomy-story" tabIndex={-1} aria-hidden={exploring || undefined}>
      {chapters.map((item, index) => <section className={`anatomy-chapter anatomy-chapter-${index} anatomy-at-${item.place}`} id={`anatomy-${index}`} key={item.name} aria-labelledby={`anatomy-title-${index}`}>
        {index === 0 && <>
          <h1 id="anatomy-title-0" className="anatomy-hero-title">{item.title}</h1>
          <div className="anatomy-hero-foot">
            <p className="anatomy-lead">Setout turns a drawing of a building detail into a 3D model with every part named.</p>
            <button className="anatomy-button" onClick={() => go(1)}>Look under the floor <Arrow down /></button>
          </div>
        </>}
        {index === 4 && <div className="anatomy-copy">
          <h2 id="anatomy-title-4" className="anatomy-count"><span className="anatomy-visually-hidden">Every part: </span>{status.components?.toLocaleString() || "6,405"}</h2>
          <p className="anatomy-body">pieces in this one house model. Each place where two of them meet is a detail.</p>
        </div>}
        {index !== 0 && index !== 4 && <div className="anatomy-copy">
          <h2 id={`anatomy-title-${index}`}>{item.title}</h2>
          <p className="anatomy-body">{item.body}</p>
          {index === 3 && <p className="anatomy-note">The hanger in this model is a Simpson LUS210, drawn from the source file. It is an illustration, not a checked connection. <a href="https://strongtie.com.au/lus-double-shear-joist-hanger" target="_blank" rel="noreferrer">About joist hangers</a></p>}
          {index === 5 && <><p className="anatomy-note">Sizes stay marked as assumptions, because a drawing is never measured from its pixels.</p><a className="anatomy-button" href="#/app">Open Studio <Arrow /></a></>}
          {index === 6 && <div className="anatomy-actions">
            <a className="anatomy-button" href="#/app">Open Studio <Arrow /></a>
            <button ref={exploreTrigger} className="anatomy-button anatomy-button-quiet" onClick={openExplore} disabled={!status.ready}>Take the house apart</button>
            <button className="anatomy-text-button" onClick={() => go(0)}>Back to the top</button>
          </div>}
        </div>}
      </section>)}
    </main>

    {exploring && <div className="anatomy-explorer" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="explorer-title">
      <button className="anatomy-button anatomy-close" onClick={() => setExploring(false)}>Back to the story <kbd>Esc</kbd></button>
      <div className="anatomy-explorer-panel">
        <h2 id="explorer-title">Take it apart</h2>
        <p className="anatomy-hint">Drag to turn. Scroll or pinch to zoom.</p>
        <label className="anatomy-range" htmlFor="anatomy-spread"><span>Pull apart</span><output htmlFor="anatomy-spread">{Math.round(spread / 1.2 * 100)}%</output></label>
        <input id="anatomy-spread" type="range" min="0" max="1.2" step="0.01" value={spread} style={{ "--fill": `${spread / 1.2 * 100}%` } as CSSProperties} onChange={(event) => { const value = Number(event.target.value); setSpread(value); scene.current?.setSpread(value); }} />
        <fieldset className="anatomy-layers"><legend>Show</legend>{layers.map((item) => <button key={item.id} aria-pressed={layer === item.id} onClick={() => pick(item.id)}><i style={{ "--layer-color": item.color } as CSSProperties} />{item.label}</button>)}</fieldset>
        <fieldset className="anatomy-closer"><legend>Look closer</legend>
          <button onClick={() => closer("floor", 12.5)}>The floor</button>
          <button onClick={() => closer("Simpson", 1.9)}>The hanger</button>
          <button onClick={() => { pick(-1); scene.current?.resetView(); }}>The whole house</button>
        </fieldset>
        <small>Live source geometry. The part groups are inferred, and nothing here is a checked design.</small>
      </div>
    </div>}
  </div>;
}
