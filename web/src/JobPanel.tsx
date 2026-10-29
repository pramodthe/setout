import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";

type Job = {
  id: string; state: string; run_id: string; version_id: string | null;
  detail_id: string | null; progress: string[]; result?: { open_issues?: string[]; states?: Record<string, number> };
  error?: { message?: string };
};
/* Compact trace rows from GET /api/runs/{id}/steps (see v3_api.step_summary). */
type Step = {
  id: string; n: number; kind: string; state: string; ms: number | null;
  purpose?: string; calls?: { name: string; arguments: string }[]; text?: string;
  tokens?: { input?: number; output?: number; cached?: number };
  tool_name?: string; arguments?: string; error?: string | null; version_id?: string | null;
  checks?: Record<string, number> | null; verdict?: string | null; issues?: string[]; images?: string[];
};

/* Every page of the trace: a run can take well over 100 steps. */
async function loadTrace(runId: string): Promise<Step[]> {
  const steps: Step[] = [];
  let after = 0;
  for (;;) {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/steps?after=${after}&limit=100`);
    if (!response.ok) throw new Error("Could not load trace");
    const page = await response.json() as { steps: Step[]; next_after: number; more: boolean };
    steps.push(...page.steps);
    if (!page.more || !page.steps.length) return steps;
    after = page.next_after;
  }
}

/* A step still pending once the run has ended was cut off by a worker crash
   or restart; the run went on from the saved steps. */
function StepRow({ step, ended }: { step: Step; ended: boolean }): JSX.Element {
  const state = ended && step.state === "pending" ? "interrupted, then resumed" : step.state;
  if (step.kind === "model") {
    const calls = step.calls?.map(c => c.name).join(", ");
    const t = step.tokens;
    return <details className="trace-step" data-kind="model">
      <summary>{step.n}. Muse {step.purpose || "turn"}{calls ? ` → ${calls}` : ""} · {state}
        {t?.input ? ` · ${t.input.toLocaleString()} in${t.cached ? ` (${t.cached.toLocaleString()} cached)` : ""} / ${(t.output || 0).toLocaleString()} out` : ""}</summary>
      {step.calls?.map((c, i) => <pre key={i}>{c.name}({c.arguments})</pre>)}
      {step.text && <p>{step.text}</p>}
    </details>;
  }
  const checks = step.checks ? Object.entries(step.checks).filter(([, n]) => n).map(([s, n]) => `${n} ${s}`).join(" · ") : "";
  const bad = !!step.error || (step.checks?.FAIL || 0) > 0 || step.verdict === "fail";
  return <details className="trace-step" data-kind="tool" open={!!step.images?.length}>
    <summary className={bad ? "trace-bad" : ""}>
      {step.n}. {step.tool_name} · {state}{checks ? ` · ${checks}` : ""}{step.verdict ? ` · critic: ${step.verdict}` : ""}
      {step.error ? ` · ${step.error}` : ""}</summary>
    <pre>{step.arguments}</pre>
    {step.issues?.map((issue, i) => <p key={i}>• {issue}</p>)}
    {!!step.images?.length && <div className="trace-images">
      {step.images.map(id => <a key={id} href={`/api/artifacts/${id}`} target="_blank" rel="noreferrer">
        <img src={`/api/artifacts/${id}`} alt="Render the agent looked at" loading="lazy" /></a>)}
    </div>}
  </details>;
}

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
      const trace = await loadTrace(next.run_id);
      if (live) { setJob(next); setSteps(trace); }
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
  return <section className="genlog" id="jobpanel" aria-label="Generation run">
    <strong>{job?.state.replaceAll("_", " ") || "Loading run…"}</strong>{" "}
    {job && ["queued", "running"].includes(job.state) && <button onClick={() => void cancel()}>Cancel</button>}
    {error && <p role="alert">{error}</p>}
    {job?.error?.message && (["queued", "running"].includes(job.state)
      ? <p>Resumed after an interruption ({job.error.message}).</p>
      : <p role="alert">{job.error.message}</p>)}
    {job?.result?.open_issues?.map(issue => <p key={issue}>{issue}</p>)}
    {job?.result?.states && <p>{Object.entries(job.result.states).map(([state, count]) => `${count} ${state.replaceAll("_", " ")}`).join(" · ")}</p>}
    {job?.version_id && job.detail_id && <a href={`#/d/${encodeURIComponent(job.detail_id)}`}>Open detail</a>}
    <details className="trace"><summary>Run trace ({steps.length} steps)</summary>
      {steps.map(step => <StepRow key={step.id} step={step} ended={!!job && !["queued", "running"].includes(job.state)} />)}
    </details>
  </section>;
}
