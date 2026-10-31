import { useCallback, useEffect, useState } from "react";
import type { JSX } from "react";
import ChatPanel from "./ChatPanel";
import { makeApi } from "./makeApi";
import type { MakeDetail, MakeModel } from "./makeApi";
import ProjectHeader from "./ProjectHeader";
import TabBar from "./TabBar";
import type { MakeTab } from "./TabBar";
import { useJobStream } from "./useJobStream";
import Viewport from "./Viewport";

const TABS_KEY = "setout-make-tabs";

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

  const onDone = useCallback((version: number) => {
    setJobId(null);
    setSelected(version);
    if (projectId) reload(projectId).catch((error: Error) => setErr(error.message));
  }, [projectId, reload]);

  const stream = useJobStream(jobId, onDone);

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
    setJobId(sent.job_id);
    await reload(projectId);
  }

  const glb = projectId && selected != null ? makeApi.glbUrl(projectId, selected) : null;

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
          busy={stream.state === "queued" || stream.state === "running"}
          onSend={send}
        />
        <Viewport url={glb} />
      </div>
    </div>
  );
}
