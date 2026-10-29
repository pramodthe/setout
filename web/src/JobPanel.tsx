import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";

type Job = {
  id: string; state: string; run_id: string; version_id: string | null;
  detail_id: string | null; progress: string[]; result?: { open_issues?: string[]; states?: Record<string, number> };
  error?: { message?: string };
};
type Step = { id: string; n: number; kind: string; tool_name: string | null; state: string; output: unknown };

export default function JobPanel({ jobId, onSettled }: { jobId: string; onSettled?: () => void }): JSX.Element {
  const [job, setJob] = useState<Job | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [error, setError] = useState("");
  const settled = useRef(onSettled);
  useEffect(() => { settled.current = onSettled; }, [onSettled]);
  useEffect(() => {
    let live = true;
    let notified = false;
    const refresh = async () => {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (!response.ok) throw new Error("Could not load run");
      const next = await response.json() as Job;
      const trace = await fetch(`/api/runs/${next.run_id}/steps?limit=100`);
      if (!trace.ok) throw new Error("Could not load trace");
      const data = await trace.json() as { steps: Step[] };
      if (live) { setJob(next); setSteps(data.steps); }
      if (live && !notified && !["queued", "running"].includes(next.state)) {
        notified = true;
        settled.current?.();
      }
    };
    const safeRefresh = () => void refresh().catch((e: Error) => live && setError(e.message));
    safeRefresh();
    const stream = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);
    stream.addEventListener("progress", safeRefresh);
    stream.addEventListener("terminal", () => { safeRefresh(); stream.close(); });
    return () => { live = false; stream.close(); };
  }, [jobId]);
  const cancel = async () => {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
    if (response.ok) setJob(await response.json() as Job);
    else setError("Could not cancel run");
  };
  return <section className="genlog" aria-label="Generation run">
    <strong>{job?.state.replaceAll("_", " ") || "Loading run…"}</strong>
    {job && ["queued", "running"].includes(job.state) && <button onClick={() => void cancel()}>Cancel</button>}
    {error && <p role="alert">{error}</p>}
    {job?.error?.message && <p role="alert">{job.error.message}</p>}
    {job?.result?.open_issues?.map(issue => <p key={issue}>{issue}</p>)}
    {job?.result?.states && <p>{Object.entries(job.result.states).map(([state, count]) => `${count} ${state.replaceAll("_", " ")}`).join(" · ")}</p>}
    {job?.version_id && job.detail_id && <a href={`#/d/${encodeURIComponent(job.detail_id)}`}>Open detail</a>}
    <details><summary>Run trace ({steps.length} steps)</summary>
      {steps.map(step => <details key={step.id}><summary>{step.n}. {step.tool_name || step.kind} · {step.state}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(step.output, null, 2)}</pre>
      </details>)}
    </details>
  </section>;
}
