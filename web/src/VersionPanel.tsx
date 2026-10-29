import { useEffect, useState } from "react";
import type { JSX } from "react";
import JobPanel from "./JobPanel";
import LessonsPanel from "./LessonsPanel";

type Version = { id: string; n: number; origin: string; format_version: string };
type History = { detail: { current_version_id: string; published_version_id: string | null }; versions: Version[] };

export default function VersionPanel({ detailId, revision, onSelect, onUpdated }: {
  detailId: string; revision: number; onSelect: (versionId: string | null) => void; onUpdated: () => void;
}): JSX.Element | null {
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void fetch(`/api/details/${encodeURIComponent(detailId)}/versions`).then(async response => {
      if (response.status === 404) return null;
      if (!response.ok) throw new Error("Could not load version history");
      return await response.json() as History;
    }).then(value => live && setHistory(value)).catch((e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, [detailId, revision]);
  // Pick up a run already going on this detail (after a reload, or one that
  // continued automatically): follow the newest.
  useEffect(() => {
    let live = true;
    void fetch(`/api/jobs?detail_id=${encodeURIComponent(detailId)}&active=1`)
      .then(async response => response.ok ? (await response.json() as { jobs: { id: string }[] }).jobs : [])
      .then(jobs => { if (live && jobs.length) setJobId(jobs[0].id); })
      .catch(() => undefined);
    return () => { live = false; };
  }, [detailId, revision]);
  const recheck = async () => {
    if (!history) return;
    const response = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "revise_detail", payload: { detail_id: detailId,
        base_version_id: history.detail.current_version_id, requested_changes: "Rebuild affected CAD, verify, render and independently critique this version." } }) });
    if (!response.ok) { setError((await response.text()).slice(0, 200)); return; }
    const result = await response.json() as { id: string };
    setJobId(result.id);
  };
  if (!history) return error ? <p role="alert">{error}</p> : null;
  return <section style={{ padding: "8px 12px" }} aria-label="Version history">
    <label>Version <select key={history.detail.current_version_id} defaultValue={history.detail.current_version_id} onChange={event => {
      onSelect(event.target.value === history.detail.current_version_id ? null : event.target.value);
    }}>
      {[...history.versions].reverse().map(version => <option key={version.id} value={version.id}>
        {version.n}{version.id === history.detail.current_version_id ? " · current" : ""}
        {version.id === history.detail.published_version_id ? " · published" : " · draft"}
        {version.format_version === "legacy-v2" ? " · archived" : ""}
      </option>)}
    </select></label>
    <button onClick={() => void recheck()}>Recheck with AI</button>
    {error && <p role="alert">{error}</p>}
    {jobId && <JobPanel jobId={jobId} onSettled={onUpdated} />}
    <LessonsPanel detailId={detailId} revision={revision} />
  </section>;
}
