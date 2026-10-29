import { useEffect, useState } from "react";
import type { JSX } from "react";

/* Lessons learnt on this detail: reviewer corrections, accepted critic
   repairs and repeated verifier failures. They stay proposed, and change
   nothing, until a reviewer switches them on for future AI runs. */
type Memory = { id: string; scope: string; scope_key: string; state: string; kind: string;
  text: string; source_version_id: string };

const SCOPE: Record<string, string> = { detail: "this detail", type: "drawings of this kind",
  manufacturer: "this manufacturer", global: "every drawing" };

export default function LessonsPanel({ detailId, revision }: { detailId: string; revision: number }): JSX.Element | null {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [reviewer, setReviewer] = useState("");
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(0);
  useEffect(() => {
    let live = true;
    void fetch(`/api/memories?detail_id=${encodeURIComponent(detailId)}`).then(async response => {
      if (!response.ok) throw new Error("Could not load lessons");
      return (await response.json() as { memories: Memory[] }).memories;
    }).then(value => live && setMemories(value)).catch((e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, [detailId, revision, loaded]);
  const activate = async (memory: Memory) => {
    setError("");
    const response = await fetch(`/api/versions/${encodeURIComponent(memory.source_version_id)}/reviews`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer: reviewer.trim(), action: "activate_memory", data: { memory_id: memory.id } }) });
    if (!response.ok) { setError((await response.text()).slice(0, 200)); return; }
    setLoaded(n => n + 1);
  };
  if (!memories.length) return error ? <p role="alert">{error}</p> : null;
  return <details className="lessons" id="lessons">
    <summary>Lessons for future runs ({memories.filter(m => m.state === "proposed").length} proposed)</summary>
    <label>Reviewer <input value={reviewer} onChange={e => setReviewer(e.target.value)} placeholder="your name" /></label>
    {memories.map(memory => <div key={memory.id} className="lesson" data-memory={memory.id}>
      <span className="lesson-meta">{memory.kind} · applies to {SCOPE[memory.scope] || memory.scope} · {memory.state}</span>
      <p>{memory.text}</p>
      {memory.state === "proposed" && <button disabled={!reviewer.trim()} onClick={() => void activate(memory)}>
        Use in future runs</button>}
    </div>)}
    {error && <p role="alert">{error}</p>}
  </details>;
}
