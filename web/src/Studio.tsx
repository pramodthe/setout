/* The studio: a drawing goes in, Muse builds it in live Blender, and a person looks through the parts and asks for changes. Apart from verified details: nothing here is checked or published. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, DragEvent, FormEvent, JSX, ReactNode } from "react";
import { StudioViewport } from "./studioViewport";
import type { Look, Tool, View } from "./studioViewport";
import "./studio.css";

type Row = { name: string; part: string; group: string; material: string | null; from: number[]; to: number[]; triangles: number; step: number };
type Feed = { kind: string; text: string };
type Scene = { id: string; title: string; state: string; step: string | null; error: string | null; parts: Row[]; feed: Feed[]; unsure: string[]; tokens: number; step_count: number; intents: string[]; model_artifact_id: string | null; preview_artifact_id: string | null; updated_at: number };
type Card = { id: string; title: string; state: string; parts: number; has_preview: boolean };

const svg = (path: ReactNode, size = 16) => <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{path}</svg>;
const CUBE = <><path d="M12 3 4 7.5v9L12 21l8-4.5v-9z" /><path d="M4 7.5 12 12l8-4.5M12 12v9" /></>;
/* A cube with the face you'd be looking at filled in, as CAD programs show their views. */
const viewCube = (face: string | null) => svg(<>{face && <path d={face} fill="currentColor" stroke="none" />}{CUBE}</>, 18);
const ICONS = {
  cube: svg(CUBE, 15),
  pointer: svg(<path d="m5 3 14 8-6 2-2 6z" />),
  hand: svg(<path d="M8 12V5.5a1.5 1.5 0 0 1 3 0V11m0-1V4.5a1.5 1.5 0 0 1 3 0V11m0-3.5a1.5 1.5 0 0 1 3 0V14c0 4-3 7-7 7-3 0-4.500-1.500-6-4l-2-3.500c-1-1.700 1.500-3 2.700-1.300L8 14" />),
  orbit: svg(<><circle cx="12" cy="12" r="2.500" /><path d="M20 8c-1.500-3-4.500-5-8-5a9 9 0 1 0 9 9" /><path d="M20 3v5h-5" /></>),
  iso: viewCube(null),
  isoOther: svg(<g transform="translate(24 0) scale(-1 1)">{CUBE}<path d="M12 12 4 7.5v9l8 4.5z" fill="currentColor" stroke="none" opacity="0.28" /></g>, 18),
  front: viewCube("M4 7.5 12 12v9l-8-4.5z"),
  side: viewCube("M12 12l8-4.5v9L12 21z"),
  top: viewCube("M12 3l8 4.5L12 12 4 7.5z"),
  perspective: svg(<><path d="M4 5.5 15 8.500v7L4 18.500z" /><path d="M15 8.500 20 10v4l-5 1.500" /><path d="M4 12h16" strokeDasharray="1.500 2.500" /></>, 18),
  undo: svg(<><path d="M8 5 3 10l5 5" /><path d="M3 10h11a6 6 0 0 1 0 12h-3" /></>),
  download: svg(<><path d="M12 4v11M7 11l5 5 5-5" /><path d="M5 20h14" /></>),
  panel: svg(<><rect x="3" y="4" width="18" height="16" rx="2.500" /><path d="M15 4v16" /></>),
  plus: svg(<path d="M12 5v14M5 12h14" />),
  send: svg(<path d="M12 19V5M6 11l6-6 6 6" />),
  chevron: svg(<path d="m9 6 6 6-6 6" />, 13),
  eye: svg(<><path d="M2 12s3.600-7 10-7 10 7 10 7-3.600 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="2.800" /></>, 14),
  eyeOff: svg(<><path d="M3 3l18 18M10.500 5.200A10 10 0 0 1 12 5c6.400 0 10 7 10 7a17 17 0 0 1-3.200 4M6.200 6.300A17 17 0 0 0 2 12s3.600 7 10 7a10 10 0 0 0 4.500-1" /></>, 14),
  clay: svg(<><circle cx="12" cy="12" r="8" /><path d="M12 4a8 8 0 0 0 0 16z" fill="currentColor" stroke="none" /></>),
  play: svg(<path d="M7 5v14l11-7z" />, 14),
  stop: svg(<rect x="6" y="6" width="12" height="12" rx="2" />, 14),
};

const VIEWS: [View, keyof typeof ICONS, string][] = [
  ["iso", "iso", "Isometric"], ["other", "isoOther", "Isometric, other side"], ["front", "front", "Front: the drawing's view"], ["side", "side", "Side"], ["top", "top", "Top"], ["perspective", "perspective", "Perspective"],
];

const pretty = (word: string) => word.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const mm = (value: number) => (Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(1));

function Top({ scenes, current, right }: { scenes: Card[]; current: string | null; right?: ReactNode }): JSX.Element {
  return (
    <header className="topbar">
      <a className="brand" href="#/"><i />Setout</a>
      <nav className="sc-tabs" aria-label="Scenes">
        {scenes.slice(0, 5).map((s) => (
          <a key={s.id} className={`sc-tab${s.id === current ? " on" : ""}`} href={`#/studio/${s.id}`} title={s.title}>{ICONS.cube}<span>{s.title}</span></a>
        ))}
        <a className="plus" href="#/studio" aria-label="New scene" title="New scene">{ICONS.plus}</a>
      </nav>
      <div className="topright">{right}</div>
    </header>
  );
}

function Home({ scenes, onFile, error }: { scenes: Card[]; onFile: (file: File) => void; error: string | null }): JSX.Element {
  const [over, setOver] = useState(false);
  const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); const file = e.dataTransfer.files[0]; if (file) onFile(file); };
  const pick = (e: ChangeEvent<HTMLInputElement>) => { const file = e.target.files?.[0]; if (file) onFile(file); };
  return (
    <main className="home">
      <div className="hero">
        <h1>Turn a section drawing into a 3D detail</h1>
        <p>Muse reads your drawing and builds every part in Blender, checking its own work as it goes.</p>
        <label className={`drop${over ? " over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}>
          <strong>Drop a section drawing here</strong>
          <span>or click to choose a PDF, PNG or JPG</span>
          <input id="studioFile" type="file" accept="application/pdf,image/png,image/jpeg" onChange={pick} />
        </label>
        {error && <p className="warm" role="alert">{error}</p>}
      </div>
      {scenes.length > 0 && (
        <section className="gallery">
          <h2>Your details</h2>
          <div className="sc-grid">
            {scenes.map((s) => (
              <a key={s.id} className="sc-card" href={`#/studio/${s.id}`}>
                <div className="thumb">{s.has_preview && <img src={`/api/studio/${s.id}/preview`} alt="" loading="lazy" />}</div>
                <div className="meta"><strong>{s.title}</strong><span>{s.state === "working" ? "Muse is building" : s.state === "failed" ? "Stopped" : `${s.parts} parts`}</span></div>
              </a>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

function Tree({ rows, selected, hidden, onSelect, onHide }: { rows: Row[]; selected: string | null; hidden: Set<string>; onSelect: (name: string) => void; onHide: (names: string[], hide: boolean) => void }): JSX.Element {
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const groups = useMemo(() => {
    const held = new Map<string, Map<string, Row[]>>();
    for (const row of rows) {
      const group = held.get(row.group) ?? new Map<string, Row[]>();
      group.set(row.part, [...(group.get(row.part) ?? []), row]);
      held.set(row.group, group);
    }
    return [...held.entries()];
  }, [rows]);
  const toggle = (key: string) => setClosed((held) => { const next = new Set(held); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  const eye = (names: string[], label: string) => {
    const off = names.every((n) => hidden.has(n));
    return <button className={`eye${off ? " off" : ""}`} aria-label={`${off ? "Show" : "Hide"} ${label}`} onClick={(e) => { e.stopPropagation(); onHide(names, !off); }}>{off ? ICONS.eyeOff : ICONS.eye}</button>;
  };
  return (
    <>
      {groups.map(([group, parts]) => {
        const all = [...parts.values()].flat().map((r) => r.name);
        const open = !closed.has(group);
        return (
          <div key={group}>
            <div className="row">
              <button className={`twist${open ? " open" : ""}`} aria-label={`${open ? "Close" : "Open"} ${pretty(group)}`} aria-expanded={open} onClick={() => toggle(group)}>{ICONS.chevron}</button>
              <button className="label" onClick={() => toggle(group)}>{ICONS.cube}<span>{pretty(group)}</span></button>
              <span className="count">{all.length}</span>{eye(all, pretty(group))}
            </div>
            {open && [...parts.entries()].map(([part, held]) => {
              const key = `${group}/${part}`;
              const many = held.length > 1;
              const expanded = many && !closed.has(key) === false;
              const names = held.map((r) => r.name);
              return (
                <div key={key} className="child">
                  <div className={`row${names.includes(selected ?? "") && !expanded ? " on" : ""}`}>
                    {many ? <button className={`twist${expanded ? " open" : ""}`} aria-label={`${expanded ? "Close" : "Open"} ${part}`} onClick={() => toggle(key)}>{ICONS.chevron}</button> : <span className="twist" />}
                    <button className="label" onClick={() => onSelect(held[0].name)}>{ICONS.cube}<span>{pretty(part)}</span></button>
                    {many && <span className="count">{held.length}</span>}{eye(names, pretty(part))}
                  </div>
                  {expanded && held.map((row) => (
                    <div key={row.name} className="child">
                      <div className={`row${row.name === selected ? " on" : ""}`}><span className="twist" /><button className="label" onClick={() => onSelect(row.name)}><span>{row.name}</span></button>{eye([row.name], row.name)}</div>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        );
      })}
    </>
  );
}

function Props({ row, intent }: { row: Row; intent?: string }): JSX.Element {
  const size = row.to.map((v, k) => v - row.from[k]);
  const centre = row.to.map((v, k) => (v + row.from[k]) / 2);
  const triple = (values: number[]) => <div className="triple">{values.map((v, k) => <div key={k}><b>{"XYZ"[k]}</b>{mm(v)}</div>)}</div>;
  return (
    <section className="props" aria-label="Selected part">
      <h3>{pretty(row.part)}</h3>
      <div className="field"><label>Position, mm</label>{triple(centre)}</div>
      <div className="field"><label>Size, mm</label>{triple(size)}</div>
      {row.material && <div className="field"><label>Material</label>{pretty(row.material)}</div>}
      {intent && <div className="field"><label>Built in the step</label>{intent}</div>}
      <p className="note">Size chosen by Muse. The drawing isn't to scale, and nothing here is verified.</p>
    </section>
  );
}

export default function Studio({ sceneId }: { sceneId: string | null }): JSX.Element {
  const stage = useRef<HTMLDivElement>(null);
  const view = useRef<StudioViewport | null>(null);
  const [scenes, setScenes] = useState<Card[]>([]);
  const [scene, setScene] = useState<Scene | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [tool, setTool] = useState<Tool>("select");
  const [look, setLook] = useState<Look>("material");
  const [panel, setPanel] = useState(true);
  const [big, setBig] = useState(false);
  const [text, setText] = useState("");
  const [spread, setSpread] = useState(0);
  const [slice, setSlice] = useState(100);
  const [playing, setPlaying] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [shot, setShot] = useState(0);
  const [current, setCurrent] = useState<View>("iso");
  const feedEnd = useRef<HTMLDivElement>(null);

  const refreshList = useCallback(async () => {
    const found = (await (await fetch("/api/studio")).json()) as { scenes: Card[] };
    setScenes(found.scenes);
  }, []);
  useEffect(() => { void refreshList().catch(() => {}); }, [refreshList, sceneId]);

  const look_ = useCallback(async () => {
    if (!sceneId) return;
    const response = await fetch(`/api/studio/${encodeURIComponent(sceneId)}`);
    if (!response.ok) throw new Error("This scene could not be found");
    const found = (await response.json()) as Scene;
    setScene(found);
    setShot(found.updated_at);
    return found;
  }, [sceneId]);

  useEffect(() => { void look_().catch((e: Error) => setError(e.message)); }, [look_]);
  const working = scene?.state === "working";
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => void look_().catch((e: Error) => setError(e.message)), 2000);
    return () => clearInterval(timer);
  }, [working, look_]);
  useEffect(() => { feedEnd.current?.scrollIntoView?.({ block: "end" }); }, [scene?.feed.length]);

  const ready = scene?.state === "ready" && !!scene.model_artifact_id;
  const model = ready ? `/api/studio/${scene.id}/model.glb?v=${scene.model_artifact_id}` : null;
  useEffect(() => {
    if (!model || !stage.current) return;
    const created = new StudioViewport(stage.current, setSelected);
    view.current = created;
    void created.load(model).catch((e: Error) => setError(e.message));
    return () => { created.dispose(); view.current = null; };
  }, [model]);

  const upload = async (file: File) => {
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const sent = await fetch("/api/studio", { method: "POST", body });
      if (!sent.ok) throw new Error(((await sent.json().catch(() => ({}))) as { detail?: string }).detail ?? "That file could not be used");
      location.hash = `#/studio/${((await sent.json()) as Scene).id}`;
    } catch (failure) {
      setError((failure as Error).message);
    }
  };

  const select = (name: string | null) => { setSelected(name); view.current?.select(name); };
  const hide = (names: string[], off: boolean) => {
    setHidden((held) => { const next = new Set(held); names.forEach((n) => (off ? next.add(n) : next.delete(n))); return next; });
    view.current?.setHidden(names, off);
  };
  const chooseTool = (next: Tool) => { setTool(next); view.current?.setTool(next); };
  const chooseLook = () => { const next = look === "clay" ? "material" : "clay"; setLook(next); view.current?.setLook(next); };
  const pick = (name: View) => { setCurrent(name); view.current?.setView(name); };
  const play = () => {
    if (playing !== null) { view.current?.stopPlay(); setPlaying(null); return; }
    setPlaying(0);
    void view.current?.play((step) => setPlaying(step < 0 ? null : step));
  };
  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (!scene || !text.trim()) return;
    setError(null);
    const response = await fetch(`/api/studio/${scene.id}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (!response.ok) { setError(((await response.json().catch(() => ({}))) as { detail?: string }).detail ?? "Muse couldn't take that"); return; }
    setText("");
    setScene((await response.json()) as Scene);
  };
  const undo = async () => {
    if (!scene) return;
    setBusy(true);
    const response = await fetch(`/api/studio/${scene.id}/undo`, { method: "POST" });
    setBusy(false);
    if (response.ok) setScene((await response.json()) as Scene); else setError("That step couldn't be undone");
  };

  if (!sceneId) return <div className="studio"><Top scenes={scenes} current={null} /><Home scenes={scenes} onFile={(f) => void upload(f)} error={error} /></div>;

  const row = scene?.parts.find((r) => r.name === selected);
  const stepText = playing !== null && scene ? scene.intents[playing - 1] : null;
  const unbuilt = !ready;
  const right = (
    <>
      <button className="ghost" onClick={play} disabled={!ready}>{playing !== null ? ICONS.stop : ICONS.play}{playing !== null ? "Stop" : "Play build"}</button>
      <a className="icon" href={ready ? `/api/studio/${scene.id}/model.glb` : undefined} download="detail.glb" aria-label="Download model" title="Download GLB" aria-disabled={!ready}>{ICONS.download}</a>
      <button className={`icon${panel ? " on" : ""}`} aria-label="Toggle the side panel" aria-pressed={panel} onClick={() => setPanel(!panel)}>{ICONS.panel}</button>
    </>
  );

  return (
    <div className="studio">
      <Top scenes={scenes} current={sceneId} right={right} />
      <div className="subbar">
        <h1>{scene?.title ?? "Loading"}</h1>
        {scene && <span className="pill">{working ? "Muse is building" : `${scene.parts.length} parts`}</span>}
        <span className="pill">Not verified</span>
        {scene && scene.tokens > 0 && <span className="pill">{scene.tokens.toLocaleString()} tokens</span>}
      </div>
      <div className={`work${panel ? "" : " wide"}`}>
        <div className="stagewrap">
          <div ref={stage} className="gl" />
          {unbuilt && (
            <div className="liveshot">
              {scene?.preview_artifact_id
                ? <img key={shot} src={`/api/studio/${scene.id}/preview?v=${shot}`} alt="Muse's latest render of the scene" />
                : <div className="thinking">{scene?.state === "failed" ? <span>This scene stopped</span> : <><div className="pulse" /><span>{scene?.step ?? "Starting"}</span></>}</div>}
            </div>
          )}
          {working && scene?.preview_artifact_id && scene.step && <div className="stepnote" role="status">{scene.step}</div>}
          {stepText && <div className="stepnote" role="status">Step {playing}: {stepText}</div>}
          {scene && <button className={`pip${big ? " big" : ""}`} onClick={() => setBig(!big)} aria-label={big ? "Make the drawing smaller" : "Make the drawing bigger"}><img src={`/api/studio/${scene.id}/drawing`} alt="Your drawing" /></button>}
          {error && <div className="alert" role="alert">{error}</div>}
          {scene?.state === "failed" && <div className="alert" role="alert">{scene.error}</div>}
          {ready && (
            <>
              <div className="rail" role="toolbar" aria-label="View tools">
                <button className={`icon${tool === "select" ? " on" : ""}`} aria-label="Select parts" data-tip="Select parts" onClick={() => chooseTool("select")}>{ICONS.pointer}</button>
                <button className={`icon${tool === "pan" ? " on" : ""}`} aria-label="Pan" data-tip="Pan" onClick={() => chooseTool("pan")}>{ICONS.hand}</button>
                <hr />
                {VIEWS.map(([name, icon, label]) => (
                  <button key={name} className={`icon${current === name ? " on" : ""}`} aria-label={label} aria-pressed={current === name} data-tip={label} onClick={() => pick(name)}>{ICONS[icon]}</button>
                ))}
                <hr />
                <button className={`icon${look === "clay" ? " on" : ""}`} aria-label="Clay view" aria-pressed={look === "clay"} data-tip="Clay view" onClick={chooseLook}>{ICONS.clay}</button>
                <button className="icon" aria-label="Undo the last step" data-tip="Undo the last step" disabled={busy || !scene?.step_count} onClick={() => void undo()}>{ICONS.undo}</button>
              </div>
              <div className="sliders">
                <label>Pull apart<input type="range" aria-label="Pull apart" min={0} max={100} value={spread} onChange={(e) => { const v = Number(e.target.value); setSpread(v); view.current?.setSpread(v / 100); }} /></label>
                <label>Cut along<input type="range" aria-label="Cut along the run" min={1} max={100} value={slice} onChange={(e) => { const v = Number(e.target.value); setSlice(v); view.current?.setSlice(v / 100); }} /></label>
              </div>
            </>
          )}
          {(ready || scene?.state === "failed") && (
            <form className="dock" onSubmit={(e) => void send(e)}>
              <div className="ask"><i /><input id="studioFeedback" aria-label="Tell Muse what to change" placeholder="Tell Muse what to change…" value={text} onChange={(e) => setText(e.target.value)} /><button className="send" aria-label="Send" disabled={!text.trim()}>{ICONS.send}</button></div>
            </form>
          )}
        </div>
        {panel && (
          <aside className="side">
            {ready ? (
              <>
                <div className="tree"><h2>Scene tree</h2>{scene && <Tree rows={scene.parts} selected={selected} hidden={hidden} onSelect={select} onHide={hide} />}</div>
                {row ? <Props row={row} intent={scene?.intents[row.step - 1]} /> : scene && scene.unsure.length > 0 ? (
                  <section className="props"><h3>Muse wasn't sure about</h3><ul style={{ margin: 0, paddingLeft: 18, color: "#6e6d68" }}>{scene.unsure.map((u) => <li key={u}>{u}</li>)}</ul></section>
                ) : null}
              </>
            ) : (
              <div className="feed" aria-live="polite">
                {scene?.feed.map((item, k) => <p key={k} className={`say ${item.kind}`}>{item.text}</p>)}
                <div ref={feedEnd} />
              </div>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
