import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { JSX } from "react";
import BottomBar from "./BottomBar";
import type { RenderQuality } from "./BottomBar";
import ChatPanel from "./ChatPanel";
import {
  diffOverrides,
  historyReducer,
  meshByName,
  meshesOf,
  type History,
  type OverrideMap,
  type TreeNode,
} from "./edits";
import { makeApi } from "./makeApi";
import type { MakeDetail, MakeModel } from "./makeApi";
import PlaybackBar from "./PlaybackBar";
import { posesAt, STEP_MS, stepLabel, installSteps } from "./playback";
import ProjectHeader from "./ProjectHeader";
import PropertiesPanel from "./PropertiesPanel";
import SceneTree from "./SceneTree";
import TabBar from "./TabBar";
import type { MakeTab } from "./TabBar";
import { useJobStream } from "./useJobStream";
import Viewport from "./Viewport";

const TABS_KEY = "setout-make-tabs";
const EMPTY: History = { past: [], present: {}, future: [] };

function readTabs(): MakeTab[] {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(TABS_KEY) || "[]") as MakeTab[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export default function MakePage({ projectId }: { projectId: string | null }): JSX.Element {
  const [tabs, setTabs] = useState<MakeTab[]>(readTabs);
  const [detail, setDetail] = useState<MakeDetail | null>(null);
  const [models, setModels] = useState<MakeModel[]>([]);
  const [profile, setProfile] = useState("muse");
  const [selected, setSelected] = useState<number | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tree, setTree] = useState<TreeNode | null>(null);
  const [part, setPart] = useState<string | null>(null);
  const [history, dispatch] = useReducer(historyReducer, EMPTY);
  const [fresh, setFresh] = useState(0);
  const [quality, setQuality] = useState<RenderQuality>("draft");
  const [jobKind, setJobKind] = useState<"build" | "render" | null>(null);
  const [renderStamp, setRenderStamp] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [sequence, setSequence] = useState(false);
  const [progress, setProgress] = useState(0);
  const presentRef = useRef(history.present);
  const progressRef = useRef(progress);
  useEffect(() => {
    presentRef.current = history.present;
    progressRef.current = progress;
  });

  useEffect(() => {
    sessionStorage.setItem(TABS_KEY, JSON.stringify(tabs));
  }, [tabs]);

  const remember = useCallback((id: string, title: string) => {
    setTabs((prev) => {
      const rest = prev.filter((tab) => tab.id !== id);
      return [...rest, { id, title: title || "Untitled" }];
    });
  }, []);

  const reload = useCallback(async (id: string) => {
    const next = await makeApi.project(id);
    setDetail(next);
    remember(id, next.project.title);
    setSelected((current) => {
      if (current != null && next.versions.some((version) => version.n === current && version.status === "done")) return current;
      const done = next.versions.filter((version) => version.status === "done");
      return next.project.active_version ?? (done.length ? done[done.length - 1].n : null);
    });
    return next;
  }, [remember]);

  useEffect(() => {
    makeApi.models().then((rows) => {
      setModels(rows);
      const ready = rows.find((row) => row.ready);
      if (ready) setProfile(ready.id);
    }).catch(() => setModels([]));
  }, []);

  useEffect(() => {
    if (projectId) return;
    let cancel = false;
    makeApi.createProject("Untitled").then((created) => {
      if (cancel) return;
      remember(created.id, created.title);
      location.hash = `#/make/${encodeURIComponent(created.id)}`;
    }).catch((error: Error) => setErr(error.message));
    return () => { cancel = true; };
  }, [projectId, remember]);

  useEffect(() => {
    if (!projectId) return;
    reload(projectId).catch((error: Error) => setErr(error.message));
  }, [projectId, reload]);

  useEffect(() => {
    if (!projectId || selected == null) {
      setTree(null);
      return;
    }
    let cancel = false;
    makeApi.tree(projectId, selected).then((next) => {
      if (!cancel) setTree(next);
    }).catch(() => {
      if (!cancel) setTree(null);
    });
    return () => { cancel = true; };
  }, [projectId, selected, fresh]);

  useEffect(() => {
    dispatch({ type: "reset" });
    setPart(null);
  }, [projectId, selected]);

  const onDone = useCallback((version: number) => {
    setJobId(null);
    setJobKind(null);
    setRenderStamp(Date.now());
    setSelected(version);
    if (projectId) reload(projectId).catch((error: Error) => setErr(error.message));
  }, [projectId, reload]);

  const stream = useJobStream(jobId, onDone);
  const busy = stream.state === "queued" || stream.state === "running";
  const meshMap = useMemo(() => {
    const map: Record<string, TreeNode> = {};
    for (const mesh of meshesOf(tree)) map[mesh.name] = mesh;
    return map;
  }, [tree]);
  const bases = useMemo(() => {
    const map: Record<string, Record<string, number>> = {};
    for (const mesh of Object.values(meshMap)) map[mesh.name] = mesh.dims || {};
    return map;
  }, [meshMap]);
  const selectedMesh = meshByName(tree, part);
  const steps = useMemo(() => installSteps(meshesOf(tree)), [tree]);
  const poses = sequence ? posesAt(steps, progress) : null;

  useEffect(() => {
    setPlaying(false);
    setSequence(false);
    setProgress(steps.length);
  }, [projectId, selected, steps.length]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const next = Math.min(steps.length, progressRef.current + (now - last) / STEP_MS);
      last = now;
      progressRef.current = next;
      setProgress(next);
      if (next >= steps.length) {
        setPlaying(false);
        return;
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, steps.length]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
      event.preventDefault();
      dispatch({ type: event.shiftKey ? "redo" : "undo" });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function merge(name: string, patch: OverrideMap[string]) {
    const present = presentRef.current;
    dispatch({
      type: "commit",
      present: { ...present, [name]: { ...present[name], ...patch } },
    });
  }

  async function createTab() {
    const created = await makeApi.createProject("Untitled");
    remember(created.id, created.title);
    location.hash = `#/make/${encodeURIComponent(created.id)}`;
  }

  async function send(text: string, file: File | null) {
    if (!projectId) return;
    let attachments: string[] = [];
    if (file) {
      const uploaded = await makeApi.upload(projectId, file);
      attachments = [uploaded.id];
    }
    const hasVersion = (detail?.versions || []).some((version) => version.status === "done");
    const sent = await makeApi.message(projectId, {
      text,
      attachments,
      mode: hasVersion ? "feedback" : "create",
      profile,
    });
    setJobKind("build");
    setJobId(sent.job_id);
    await reload(projectId);
  }

  async function apply() {
    if (!projectId || selected == null) return;
    const body = diffOverrides(meshMap, presentRef.current);
    if (!body.overrides.length) return;
    const sent = await makeApi.overrides(projectId, selected, body);
    setJobKind("build");
    setJobId(sent.job_id);
  }

  async function renderScene() {
    if (!projectId || selected == null) return;
    const sent = await makeApi.render(projectId, selected, quality);
    setJobKind("render");
    setJobId(sent.job_id);
  }

  function togglePlay() {
    if (!steps.length) return;
    if (playing) {
      setPlaying(false);
      return;
    }
    setSequence(true);
    if (progressRef.current >= steps.length) {
      progressRef.current = 0;
      setProgress(0);
    }
    setPlaying(true);
  }

  const version = detail?.versions.find((item) => item.n === selected) || null;
  const glb = projectId && selected != null ? `${makeApi.glbUrl(projectId, selected)}?r=${fresh}` : null;
  const downloads = projectId && selected != null ? {
    glb: makeApi.glbUrl(projectId, selected),
    tree: makeApi.treeUrl(projectId, selected),
    preview: makeApi.previewUrl(projectId, selected),
    render: `${makeApi.renderUrl(projectId, selected)}?r=${renderStamp}`,
  } : null;
  const rendering = jobKind === "render" && stream.state !== "failed";
  const renderStatus = jobKind === "render"
    ? stream.state === "failed"
      ? (stream.lines[stream.lines.length - 1]?.text || "Render failed")
      : `Rendering with Blender… ${stream.state || "queued"}`
    : null;
  const canApply = diffOverrides(meshMap, history.present).overrides.length > 0;

  return (
    <div className="make">
      <TabBar tabs={tabs} active={projectId} onNew={() => { void createTab(); }} />
      <ProjectHeader
        title={detail?.project.title || ""}
        versions={detail?.versions || []}
        selected={selected}
        onSelect={setSelected}
        models={models}
        profile={profile}
        onProfile={setProfile}
      />
      {err && <p className="make-err">{err}</p>}
      <div className="make-body">
        <ChatPanel
          messages={detail?.messages || []}
          lines={stream.lines}
          attempt={stream.attempt}
          state={stream.state}
          busy={busy}
          onSend={send}
        />
        <Viewport
          url={glb}
          selected={part}
          edits={history.present}
          bases={bases}
          playback={poses}
          mode="translate"
          onSelect={setPart}
          onCommit={(name, location, rotation) => merge(name, { location, rotation_deg: rotation })}
        >
          <PlaybackBar
            playing={playing}
            label={sequence ? stepLabel(steps, progress) : steps.length ? `${steps.length} parts` : "No parts"}
            progress={sequence ? progress : steps.length}
            count={steps.length}
            onToggle={togglePlay}
            onScrub={(value) => {
              setPlaying(false);
              setSequence(true);
              progressRef.current = value;
              setProgress(value);
            }}
          />
          {version?.has_render && downloads && (
            <aside className="make-render" data-make="render-panel">
              <img data-make="render-image" src={downloads.render} alt="Blender render" />
              <a data-make="download-render" href={downloads.render} download="render.png">Download render</a>
            </aside>
          )}
        </Viewport>
        <aside className="make-side">
          <div className="make-tools">
            <button type="button" data-make="undo" onClick={() => dispatch({ type: "undo" })} disabled={!history.past.length}>Undo</button>
            <button type="button" data-make="redo" onClick={() => dispatch({ type: "redo" })} disabled={!history.future.length}>Redo</button>
          </div>
          <SceneTree tree={tree} selected={part} onSelect={setPart} />
          <PropertiesPanel
            mesh={selectedMesh}
            pending={part ? history.present[part] || null : null}
            onDim={(key, value) => {
              if (!part || !selectedMesh) return;
              merge(part, { dims: { ...(selectedMesh.dims || {}), ...(presentRef.current[part]?.dims || {}), [key]: value } });
            }}
            onLocation={(index, value) => {
              if (!part || !selectedMesh) return;
              const location = [...(presentRef.current[part]?.location || selectedMesh.location || [0, 0, 0])] as [number, number, number];
              location[index] = value;
              merge(part, { location });
            }}
            onRotation={(index, value) => {
              if (!part || !selectedMesh) return;
              const rotation = [...(presentRef.current[part]?.rotation_deg || selectedMesh.rotation_deg || [0, 0, 0])] as [number, number, number];
              rotation[index] = value;
              merge(part, { rotation_deg: rotation });
            }}
            onApply={() => { void apply().catch((error: Error) => setErr(error.message)); }}
            canApply={canApply}
            busy={busy}
          />
        </aside>
      </div>
      <BottomBar
        quality={quality}
        onQuality={setQuality}
        onRefresh={() => setFresh((n) => n + 1)}
        onRender={() => { void renderScene().catch((error: Error) => setErr(error.message)); }}
        busy={busy}
        rendering={rendering}
        status={renderStatus}
        glbUrl={downloads?.glb || null}
        treeUrl={downloads?.tree || null}
        previewUrl={downloads?.preview || null}
      />
    </div>
  );
}
