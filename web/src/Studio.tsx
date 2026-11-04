/* The studio: a drawing goes in, Muse builds it in live Blender, and a person looks through the parts and asks for changes. Nothing here is verified, and the page says so. */

import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, FormEvent, JSX } from "react";
import "@fontsource-variable/inter-tight";
import AppBar from "./AppBar";
import ConfirmDelete from "./ConfirmDelete";
import Home from "./Home";
import { ICONS } from "./icons";
import { Props } from "./Inspector";
import SceneTree from "./SceneTree";
import { wholeSteps } from "./scene";
import type { Card, Scene } from "./scene";
import { watchScene } from "./scenePolling";
import { StudioViewport } from "./viewport";
import type { Look, Tool, View } from "./viewport";

const VIEWS: [View, keyof typeof ICONS, string][] = [
  ["iso", "iso", "Isometric"], ["other", "isoOther", "Isometric, other side"], ["front", "front", "Front: the drawing's view"], ["side", "side", "Side"], ["top", "top", "Top"], ["perspective", "perspective", "Perspective"],
];


export default function Studio({ sceneId }: { sceneId: string | null }): JSX.Element {
  const stage = useRef<HTMLDivElement>(null);
  const view = useRef<StudioViewport | null>(null);
  const [scenes, setScenes] = useState<Card[]>([]);
  const [scene, setScene] = useState<Scene | null>(null);
  const [doomed, setDoomed] = useState<{ id: string; title: string } | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [tool, setTool] = useState<Tool>("select");
  const [look, setLook] = useState<Look>("material");
  const [panel, setPanel] = useState(true);
  const [big, setBig] = useState(false);
  const [text, setText] = useState("");
  const [spread, setSpread] = useState(0);
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

  const working = scene?.state === "working";
  useEffect(() => {
    if (!sceneId) return;
    return watchScene(`/api/studio/${encodeURIComponent(sceneId)}`, (found) => {
      setScene(found);
      setShot(found.updated_at);
    }, setLoadError);
  }, [sceneId, working]);
  useEffect(() => { feedEnd.current?.scrollIntoView?.({ block: "end" }); }, [scene?.feed.length]);

  const ready = scene?.state === "ready" && !!scene.model_artifact_id;
  const viewable = ready || !!scene?.live_model;
  const model = viewable && scene ? `/api/studio/${scene.id}/model.glb?v=${scene.model_artifact_id}` : null;
  useEffect(() => {
    if (!model || !stage.current) return;
    const created = new StudioViewport(stage.current, setSelected);
    view.current = created;
    void created.load(model).catch((e: Error) => setError(e.message));
    return () => { created.dispose(); view.current = null; };
  }, [model]);

  const upload = async (file: File, method: "serial" | "hybrid") => {
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("method", method);
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
  const tell = async (words: string) => {
    if (!scene || !words.trim()) return;
    setError(null);
    const response = await fetch(`/api/studio/${scene.id}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: words }) });
    if (!response.ok) { setError(((await response.json().catch(() => ({}))) as { detail?: string }).detail ?? "Muse couldn't take that"); return; }
    setText("");
    setScene((await response.json()) as Scene);
  };
  const send = (e: FormEvent) => { e.preventDefault(); void tell(text); };
  const remove = async () => {
    if (!doomed) return;
    const { id } = doomed;
    setDoomed(null);
    const response = await fetch(`/api/studio/${id}`, { method: "DELETE" });
    if (!response.ok) { setError(((await response.json().catch(() => ({}))) as { detail?: string }).detail ?? "That scene couldn't be deleted"); return; }
    if (id === sceneId) location.hash = "#/app"; else await refreshList();
  };
  const closeDelete = useCallback(() => setDoomed(null), []);
  const rename = async () => {
    const title = name?.trim();
    setName(null);
    if (!scene || !title || title === scene.title) return;
    const response = await fetch(`/api/studio/${scene.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
    if (response.ok) setScene((await response.json()) as Scene); else setError("That name couldn't be saved");
  };
  const undo = async () => {
    if (!scene) return;
    setBusy(true);
    const response = await fetch(`/api/studio/${scene.id}/undo`, { method: "POST" });
    setBusy(false);
    if (response.ok) setScene((await response.json()) as Scene); else setError("That step couldn't be undone");
  };

  if (!sceneId) return <div className="studio"><AppBar right={<a className="storylink" href="#/">The story</a>} /><Home scenes={scenes} onFile={(f, method) => void upload(f, method)} onDelete={setDoomed} error={error} />{doomed && <ConfirmDelete title={doomed.title} onCancel={closeDelete} onConfirm={() => void remove()} />}</div>;

  const row = scene?.parts.find((r) => r.name === selected);
  const stepText = playing !== null && scene ? scene.intents[playing - 1] : null;
  const progress = scene?.queued ? "Queued for building" : scene?.step ?? "Connecting…";
  const unbuilt = !viewable;
  const right = (
    <>
      {scene && !working && <button className="icon" aria-label="Delete this scene" title="Delete" onClick={() => setDoomed({ id: scene.id, title: scene.title })}>{ICONS.trash}</button>}
      <a className="icon" href={ready ? `/api/studio/${scene.id}/model.glb` : undefined} download="detail.glb" aria-label="Download model" title="Download GLB" aria-disabled={!ready}>{ICONS.download}</a>
      <button className={`icon${panel ? " on" : ""}`} aria-label="Toggle the side panel" aria-pressed={panel} onClick={() => setPanel(!panel)}>{ICONS.panel}</button>
      <button className="solid" onClick={play} disabled={!ready}>{playing !== null ? "Stop" : "Play build"}<span className="square">{playing !== null ? ICONS.stop : ICONS.play}</span></button>
    </>
  );

  return (
    <div className="studio">
      <AppBar right={right}>
        <div className="scenehead">
          <a className="allback" href="#/app" aria-label="All details" title="All details">{ICONS.back}</a>
          {name === null ? <h1>{scene?.title ?? "Loading"}</h1> : (
            <form onSubmit={(e) => { e.preventDefault(); void rename(); }}>
              <input className="rename" aria-label="Scene name" autoFocus maxLength={60} value={name} onChange={(e) => setName(e.target.value)} onBlur={() => void rename()} onKeyDown={(e) => { if (e.key === "Escape") setName(null); }} />
            </form>
          )}
          {scene && name === null && <button className="icon" aria-label="Rename" title="Rename" onClick={() => setName(scene.title)}>{ICONS.pencil}</button>}
          {scene && <span className="quiet">{working ? (scene.queued ? "Queued" : "Muse is building") : `${scene.parts.length} parts`}</span>}
          <span className="tag">Not verified</span>
        </div>
      </AppBar>
      <div className={`work${panel ? "" : " wide"}`}>
        <div className="stagewrap">
          <div ref={stage} className="gl" />
          {unbuilt && (
            <div className="liveshot">
              {scene?.preview_artifact_id
                ? <img key={shot} src={`/api/studio/${scene.id}/preview?v=${shot}`} alt="Muse's latest render of the scene" />
                : <div className="thinking">{scene?.state === "failed" ? <span>This scene stopped</span> : <><div className="pulse" /><span>{progress}</span></>}</div>}
            </div>
          )}
          {working && scene?.preview_artifact_id && <div className="stepnote" role="status">{progress}</div>}
          {stepText && <div className="stepnote" role="status">Step {playing}: {stepText}</div>}
          {scene && <button className={`pip${big ? " big" : ""}`} onClick={() => setBig(!big)} aria-label={big ? "Make the drawing smaller" : "Make the drawing bigger"}><img src={`/api/studio/${scene.id}/drawing`} alt="Your drawing" /></button>}
          {error && <div className="alert" role="alert">{error}</div>}
          {!error && loadError && <div className="alert" role="status">{loadError}</div>}
          {scene?.state === "failed" && <div className="alert" role="alert">{scene.error}</div>}
          {viewable && (
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
                <button className="icon" aria-label="Undo the last step" data-tip="Undo the last step" disabled={busy || working || !scene?.step_count} onClick={() => void undo()}>{ICONS.undo}</button>
              </div>
              <div className="sliders">
                <label>Pull apart<input type="range" aria-label="Pull apart" min={0} max={100} value={spread} style={{ "--fill": `${spread}%` } as CSSProperties} onChange={(e) => { const v = Number(e.target.value); setSpread(v); view.current?.setSpread(v / 100); }} /></label>
              </div>
            </>
          )}
          {(ready || scene?.state === "failed") && (
            <form className="dock" onSubmit={send}>
              <div className="ask"><input id="studioFeedback" aria-label="Tell Muse what to change" placeholder="Tell Muse what to change" value={text} onChange={(e) => setText(e.target.value)} /><button className="send" aria-label="Send" disabled={!text.trim()}>{ICONS.send}</button></div>
            </form>
          )}
        </div>
        {panel && (
          <aside className="side">
            {ready ? (
              <>
                <div className="tree"><h2>Scene tree</h2>{scene && <SceneTree rows={scene.parts} selected={selected} hidden={hidden} onSelect={select} onHide={hide} />}</div>
                {row && <Props row={row} intent={scene?.intents[row.step - 1]} />}
              </>
            ) : (
              <div className="feed" aria-live="polite">
                <h2>Build</h2>
                <ul>
                  {(scene ? wholeSteps(scene.feed, scene.intents) : []).map((item, k) => <li key={k} className={`say ${item.kind}`}>{item.text}</li>)}
                </ul>
                <div ref={feedEnd} />
              </div>
            )}
          </aside>
        )}
      </div>
      {doomed && <ConfirmDelete title={doomed.title} onCancel={closeDelete} onConfirm={() => void remove()} />}
    </div>
  );
}
