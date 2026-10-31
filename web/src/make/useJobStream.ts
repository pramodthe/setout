import { useEffect, useRef, useState } from "react";

export interface JobLine {
  kind: string;
  text: string;
}

const RETRIES = 6;

/** Live status for one make job. A dropped connection reconnects and continues after the last log. */
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
    let stopped = false;
    let after = 0;
    let retries = 0;
    let source: EventSource | null = null;
    let timer = 0;
    const add = (kind: string, text: string) => {
      if (!text) return;
      setLines((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.kind === kind && last.text === text) return prev;
        return [...prev.slice(-20), { kind, text }];
      });
    };
    const remember = (event: Event) => {
      const id = Number((event as MessageEvent).lastEventId);
      if (Number.isFinite(id) && id > after) after = id;
    };
    const connect = () => {
      if (stopped) return;
      source = new EventSource(`/api/make/jobs/${encodeURIComponent(jobId)}/events?after=${after}`);
      source.addEventListener("status", (event) => {
        remember(event);
        const body = JSON.parse((event as MessageEvent).data) as { state: string; attempt: number };
        setState(body.state);
        if (body.attempt) setAttempt(body.attempt);
        retries = 0;
      });
      source.addEventListener("attempt", (event) => {
        remember(event);
        const body = JSON.parse((event as MessageEvent).data) as { attempt: number };
        setAttempt(body.attempt);
      });
      source.addEventListener("log", (event) => {
        remember(event);
        const body = JSON.parse((event as MessageEvent).data) as { message?: string; kind?: string };
        add(body.kind || "log", body.message || "");
      });
      source.addEventListener("done", (event) => {
        remember(event);
        const body = JSON.parse((event as MessageEvent).data) as { version: number };
        setState("done");
        add("done", `done — version ${body.version}`);
        stopped = true;
        source?.close();
        onDoneRef.current(body.version);
      });
      source.addEventListener("failed", (event) => {
        if (!(event instanceof MessageEvent) || !event.data) return;
        const body = JSON.parse(event.data) as { message?: string };
        setState("failed");
        add("error", body.message || "failed");
        stopped = true;
        source?.close();
      });
      source.onerror = () => {
        if (stopped) return;
        source?.close();
        retries += 1;
        if (retries > RETRIES) {
          setState("failed");
          add("error", "Lost the status stream.");
          stopped = true;
          return;
        }
        setState("reconnecting");
        timer = window.setTimeout(connect, Math.min(400 * retries, 4000));
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      source?.close();
    };
  }, [jobId]);

  return { lines, attempt, state };
}
