/* Sidebar installation guide: Muse answers as a tour of narrated steps whose
   actions drive the 3D viewer. Numbers the grounding check could not find in
   the detail are flagged on the step, never hidden. */

import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import { api } from "./api";
import type { GuideAction, GuideResult, GuideStep } from "./types";

interface Msg {
  role: "user" | "guide";
  text: string;
  steps?: GuideStep[];
  follow?: string;
  retried?: boolean;
}

const QUICK = [
  "Walk me through the install, step by step",
  "What came from the drawing, and what is assumed?",
  "Which layer is covered by which, and why that order?",
];

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function Guide(p: {
  detailId: string;
  run: (a: GuideAction) => void;
  off?: boolean; // AI not configured on this server (e.g. browse-only demo)
}): JSX.Element {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [cur, setCur] = useState<{ m: number; s: number } | null>(null);
  const stop = useRef(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // block body on purpose: smooth scrollIntoView returns a Promise in current
    // Chrome, and an effect must return nothing or a cleanup function
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [msgs, cur]);
  useEffect(
    () => () => {
      stop.current = true;
    },
    [],
  );

  const play = async (mi: number, steps: GuideStep[]) => {
    stop.current = false;
    for (let i = 0; i < steps.length; i++) {
      if (stop.current) break;
      setCur({ m: mi, s: i });
      for (const a of steps[i].actions) p.run(a);
      await pause(Math.min(9000, 1800 + 38 * steps[i].say.length));
    }
    setCur(null);
  };

  const ask = async (question: string) => {
    if (!question.trim() || busy) return;
    stop.current = true;
    const history = msgs.map((m) => ({
      role: m.role,
      text: m.steps ? m.steps.map((s) => s.say).join(" ") : m.text,
    }));
    setMsgs((x) => [...x, { role: "user", text: question }]);
    setQ("");
    setBusy(true);
    try {
      const job = await api.waitJob(await api.guide(p.detailId, question, history));
      if (job.state !== "done" || !job.result) throw new Error(job.error || "no answer");
      const r = job.result as GuideResult;
      const mi = msgs.length + 1;
      setMsgs((x) => [...x, { role: "guide", text: "", steps: r.steps, follow: r.follow_up, retried: r.retried }]);
      void play(mi, r.steps);
    } catch (e) {
      setMsgs((x) => [...x, { role: "guide", text: "Sorry — " + (e as Error).message.slice(0, 160) }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="guide">
      <div className="ghead">
        <b>AI installation guide</b>
        <span>Muse narrates and drives the 3D. Numbers are checked against this detail.</span>
      </div>
      <div className="gmsgs" id="guideMsgs">
        {p.off && (
          <div className="gempty k">The AI guide is switched off on this server. Everything else works: orbit,
            explode, cut and step through the install.</div>
        )}
        {msgs.length === 0 && !p.off && (
          <div className="gempty">
            {QUICK.map((x) => (
              <button key={x} className="chip" onClick={() => void ask(x)}>
                {x}
              </button>
            ))}
          </div>
        )}
        {msgs.map((m, mi) =>
          m.role === "user" ? (
            <div key={mi} className="gmsg user">{m.text}</div>
          ) : (
            <div key={mi} className="gmsg ai">
              {m.text}
              {m.steps?.map((s, si) => (
                <div
                  key={si}
                  className={"gstep" + (cur && cur.m === mi && cur.s === si ? " on" : "")}
                  onClick={() => {
                    stop.current = true;
                    setCur({ m: mi, s: si });
                    for (const a of s.actions) p.run(a);
                  }}
                >
                  <span className="n">{si + 1}</span>
                  <span>{s.say}</span>
                  {s.ungrounded.length > 0 && (
                    <span className="warn" title="not found in this detail">
                      unverified: {s.ungrounded.join(", ")}
                    </span>
                  )}
                </div>
              ))}
              {m.retried && <div className="gnote">grounding check asked the guide to re-answer once</div>}
              {m.follow && (
                <button className="chip" onClick={() => void ask(m.follow!)}>
                  {m.follow}
                </button>
              )}
            </div>
          ),
        )}
        {busy && <div className="gmsg ai thinking">thinking…</div>}
        <div ref={end} />
      </div>
      <div className="gctl">
        {cur && (
          <button onClick={() => (stop.current = true)} title="stop the tour">
            ■ stop
          </button>
        )}
        <input
          id="guideInput"
          placeholder="Ask the guide… e.g. what goes on first?"
          value={q}
          disabled={busy || p.off}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void ask(q)}
        />
        <button id="guideSend" className="primary" disabled={busy || !q.trim()} onClick={() => void ask(q)}>
          Ask
        </button>
      </div>
    </aside>
  );
}
