import { useEffect, useRef, useState } from "react";

export interface JobLine {
  kind: string;
  text: string;
}

/** Live status, attempt and log lines for one make job. Closes on done or error. */
export function useJobStream(jobId: string | null, onDone: (version: number) => void): {
  lines: JobLine[];
  attempt: number | null;
  state: string | null;
} {
  const [lines, setLines] = useState<JobLine[]>([]);
  const [attempt, setAttempt] = useState<number | null>(null);
  const [state, setState] = useState<string | null>(null);
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onDoneRef.current = onDone;
  });

  useEffect(() => {
    if (!jobId) return;
    setLines([]);
    setAttempt(null);
    setState("queued");
    const source = new EventSource(`/api/make/jobs/${encodeURIComponent(jobId)}/events`);
    const add = (kind: string, text: string) => {
      if (!text) return;
      setLines((prev) => [...prev.slice(-20), { kind, text }]);
    };
    source.addEventListener("status", (event) => {
      const body = JSON.parse((event as MessageEvent).data) as { state: string; attempt: number };
      setState(body.state);
      setAttempt(body.attempt);
    });
    source.addEventListener("attempt", (event) => {
      const body = JSON.parse((event as MessageEvent).data) as { attempt: number };
      setAttempt(body.attempt);
      add("attempt", `attempt ${body.attempt}`);
    });
    source.addEventListener("log", (event) => {
      const body = JSON.parse((event as MessageEvent).data) as { message?: string; kind?: string };
      add(body.kind || "log", body.message || "");
    });
    source.addEventListener("done", (event) => {
      const body = JSON.parse((event as MessageEvent).data) as { version: number };
      setState("done");
      add("done", `done — version ${body.version}`);
      onDoneRef.current(body.version);
      source.close();
    });
    source.addEventListener("error", (event) => {
      if (!(event instanceof MessageEvent) || !event.data) return;
      const body = JSON.parse(event.data) as { message?: string };
      setState("failed");
      add("error", body.message || "failed");
      source.close();
    });
    return () => source.close();
  }, [jobId]);

  return { lines, attempt, state };
}
