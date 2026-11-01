/* A section to a planned 3D assembly. Muse reads the drawing and chooses parts, a person confirms or corrects
   that reading, and the engine builds it. Apart from verified details: nothing here is checked or published. */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, JSX } from "react";
import { Viewport3D } from "./viewport";
import type { Scene } from "./types";

const CATEGORIES = ["brick", "block", "stone", "concrete", "mortar", "adhesive", "tile", "board", "timber", "insulation", "membrane", "flashing", "sealant",
  "fixing", "wall tie", "reinforcement", "steel section", "metal trim", "window", "door", "glazing", "weep", "drainage"];
const FORMS: [string, string][] = [["solid", "one piece"], ["units", "repeated units"], ["sheet", "thin sheet"], ["spaced", "spaced objects"], ["none", "leave out"]];

type Part = { id: string; name: string; category: string; form: string; seen: string; reason: string };
type Choice = { component: number; bond: string; spacing_mm: number; why: string };
type Hit = { id: number; name: string; size: number[] };
type Relation = { subject: string; relation: string; target: string; second_target: string; before: string; after: string };
type Result = { contract: Relation[]; clashes: [string, string, number][]; notes: string[]; misfits: string[]; broken_before: number; broken_after: number; binding: number };
type Assembly = {
  id: string; state: string; step?: string; error?: string; doc_id: string; crop_id: string; tokens?: number; verdict?: string | null;
  plan?: { assembly: string; parts: Part[]; unsure: string[]; faults?: string[] } | null;
  shortlists?: { part: string; wanted: string; candidates: Hit[] }[] | null;
  choices?: Record<string, Choice> | null; result?: Result | null;
};
type Edit = { category?: string; form?: string };

const send = async (path: string, body: unknown): Promise<Assembly> => {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) throw new Error((await response.text()).slice(0, 300));
  return (await response.json()) as Assembly;
};

export default function AssemblyView({ assemblyId }: { assemblyId: string | null }): JSX.Element {
  const root = useRef<HTMLDivElement>(null);
  const viewport = useRef<Viewport3D | null>(null);
  const [assembly, setAssembly] = useState<Assembly | null>(null);
  const [scene, setScene] = useState<Scene | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState(() => { try { return localStorage.getItem("setout-reviewer") ?? ""; } catch { return ""; } });
  const [reading, setReading] = useState("");
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [correction, setCorrection] = useState("");
  const [note, setNote] = useState("");
  const [explode, setExplode] = useState(0);

  const look = useCallback(async () => {
    if (!assemblyId) return;
    const response = await fetch(`/api/assemblies/${encodeURIComponent(assemblyId)}`);
    if (!response.ok) throw new Error("This assembly could not be found");
    const found = (await response.json()) as Assembly;
    setAssembly(found);
    setReading(found.plan?.assembly ?? "");
    if (found.state === "done") setScene((await (await fetch(`/api/assemblies/${encodeURIComponent(assemblyId)}/scene`)).json()) as Scene);
  }, [assemblyId]);

  useEffect(() => { void look().catch((e: Error) => setError(e.message)); }, [look]);

  const running = assembly?.state === "planning" || assembly?.state === "building";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void look().catch((e: Error) => setError(e.message)), 2000);
    return () => clearInterval(timer);
  }, [running, look]);

  useEffect(() => {
    if (!scene || !root.current) return;
    const view = new Viewport3D(root.current, () => {});
    view.loadScene(scene, { intro: false });
    view.skipIntro();
    view.setLabels(false);
    view.setView("iso", 0);
    viewport.current = view;
    return () => { view.dispose(); viewport.current = null; };
  }, [scene]);

  const act = async (step: () => Promise<Assembly>) => {
    setError(null);
    try {
      try { localStorage.setItem("setout-reviewer", reviewer); } catch { /* the name is only a convenience */ }
      const next = await step();
      setAssembly(next);
      setEdits({});
      setCorrection("");
    } catch (failure) {
      setError((failure as Error).message);
    }
  };

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const sent = await fetch("/api/documents", { method: "POST", body });
      if (!sent.ok) throw new Error((await sent.text()).slice(0, 200));
      const document_ = (await sent.json()) as { id: string; figures: { crop_id: string }[] };
      const started = await send("/api/assemblies", { doc_id: document_.id, crop_id: document_.figures[0].crop_id });
      location.hash = `#/assembly/${started.id}`;
    } catch (failure) {
      setError((failure as Error).message);
    }
  };

  const move = (value: number) => {
    setExplode(value);
    if (viewport.current && scene) {
      viewport.current.setState({ visible: Object.fromEntries(scene.solids.map((s) => [s.id, true])), seqOn: false,
        seqIdx: scene.install_order.length - 1, explode: value, clipOn: false, clipZ: 0 });
    }
  };

  const edit = (id: string, field: keyof Edit, value: string) => setEdits((held) => ({ ...held, [id]: { ...held[id], [field]: value } }));
  const plan = assembly?.plan;
  const drawing = assembly ? `/api/documents/${assembly.doc_id}/figures/${encodeURIComponent(assembly.crop_id)}` : null;
  const result = assembly?.result;

  return (
    <div className="draft assembly">
      <header className="dvhead">
        <a href="#/" className="back">← Library</a>
        <div className="ttl">
          <div className="path">Planned detail · not verified · can't be published</div>
          <h1>{plan?.assembly || "3D from a section"}</h1>
        </div>
        {assembly?.tokens ? <span className="vsum">{assembly.tokens.toLocaleString()} tokens</span> : null}
      </header>

      {!assemblyId && <p className="draftnote"><label>Choose a one-page PDF of a section <input id="assemblyPdf" type="file" accept="application/pdf" onChange={(e) => void upload(e)} /></label></p>}
      {running && <p className="draftnote" role="status">{assembly?.step}…</p>}
      {assembly?.state === "failed" && <p className="liberr" role="alert">{assembly.error}</p>}
      {error && <p className="liberr" role="alert">{error}</p>}

      {assembly?.state === "awaiting_confirmation" && plan && (
        <div className="asmgrid">
          <section className="asmcard"><h2>Your drawing</h2>{drawing && <img src={drawing} alt="Uploaded section drawing" className="asmdrawing" />}</section>
          <section className="asmcard">
            <h2>Is this right? Nothing is built until you say so</h2>
            <label className="asmfield">Muse reads this as
              <textarea id="assemblyReading" rows={2} value={reading} onChange={(e) => setReading(e.target.value)} /></label>
            {plan.unsure.length > 0 && <div className="asmunsure"><strong>Muse is unsure about</strong><ul>{plan.unsure.map((u) => <li key={u}>{u}</li>)}</ul></div>}
            {plan.faults && plan.faults.length > 0 && <div className="asmunsure"><strong>Problems with this plan</strong><ul>{plan.faults.map((u) => <li key={u}>{u}</li>)}</ul></div>}
            <table className="asmtable">
              <thead><tr><th>Part</th><th>What it is</th><th>How it's built</th><th>Catalogue part</th></tr></thead>
              <tbody>
                {plan.parts.filter((p) => p.form !== "none").map((part) => {
                  const choice = assembly.choices?.[part.id];
                  const list = assembly.shortlists?.find((s) => s.part === part.id);
                  const hit = list?.candidates.find((c) => c.id === choice?.component);
                  return (
                    <tr key={part.id}>
                      <td>{part.name}{part.seen === "implied" && <span className="asmimplied"> implied</span>}<small>{part.reason}</small></td>
                      <td><select aria-label={`What ${part.name} is`} value={edits[part.id]?.category ?? part.category} onChange={(e) => edit(part.id, "category", e.target.value)}>
                        {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}</select></td>
                      <td><select aria-label={`How ${part.name} is built`} value={edits[part.id]?.form ?? part.form} onChange={(e) => edit(part.id, "form", e.target.value)}>
                        {FORMS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></td>
                      <td>{hit ? <span className="asmhit"><img src={`/api/catalogue/${hit.id}/thumb`} alt="" />{hit.name}<small>{list?.wanted} wanted · {hit.size.map((s) => Math.round(s * 1000)).join(" × ")} mm</small></span> : <small>from the drawing</small>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <label className="asmfield">Your name <input id="assemblyReviewer" value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></label>
            <p><button id="assemblyConfirm" className="btn primary" disabled={!reviewer.trim()} onClick={() => void act(() => send(`/api/assemblies/${assembly.id}/confirm`, {
              reviewer, assembly: reading, parts: Object.entries(edits).map(([id, values]) => ({ id, ...values })) }))}>Confirm and build</button></p>
            <label className="asmfield">Or tell Muse what's wrong, and it reads the drawing again
              <textarea id="assemblyCorrection" rows={2} value={correction} onChange={(e) => setCorrection(e.target.value)} /></label>
            <p><button className="btn" disabled={!reviewer.trim() || !correction.trim()} onClick={() => void act(() => send(`/api/assemblies/${assembly.id}/correct`, { reviewer, text: correction }))}>Read again</button></p>
          </section>
        </div>
      )}

      {assembly?.state === "done" && (
        <>
          <p className="draftnote">
            Outlines come from your drawing, parts from the catalogue, and sizes are assumed. Ghosted parts aren't in the drawing: Muse says they are needed.{" "}
            <label>explode <input id="assemblyExplode" type="range" min={0} max={300} value={explode} onChange={(e) => move(Number(e.target.value))} /></label>
          </p>
          <div className="asmgrid">
            <section className="asmcard"><h2>Your drawing</h2>{drawing && <img src={drawing} alt="Uploaded section drawing" className="asmdrawing" />}</section>
            <section className="asmcard"><div ref={root} id="gl" style={{ position: "relative", width: "100%", height: "66vh" }} /></section>
          </div>
          {result && (
            <div className="asmgrid">
              <section className="asmcard">
                <h2>Does this look right?</h2>
                {assembly.verdict && <p role="status">{assembly.verdict === "good" ? "Marked good: similar drawings will learn from it." : "Marked wrong: it won't be used as an example."}</p>}
                <label className="asmfield">Your name <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></label>
                <label className="asmfield">What's wrong or right about it <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></label>
                <p className="asmbuttons">
                  <button id="assemblyGood" className="btn primary" disabled={!reviewer.trim()} onClick={() => void act(() => send(`/api/assemblies/${assembly.id}/verdict`, { reviewer, verdict: "good", note }))}>Good, learn from this</button>
                  <button className="btn" disabled={!reviewer.trim()} onClick={() => void act(() => send(`/api/assemblies/${assembly.id}/verdict`, { reviewer, verdict: "wrong", note }))}>Not right</button>
                </p>
              </section>
              <section className="asmcard">
                <h2>What the engine reports</h2>
                <p>Relationships broken: {result.broken_before} before repair, {result.broken_after} after, of {result.binding}. Clashes: {result.clashes.length}.</p>
                <ul>
                  {result.contract.filter((c) => c.after === "broken").map((c) => <li key={`${c.subject}${c.relation}${c.target}`}>Still broken: {c.subject} {c.relation.replace("_", " ")} {c.target}</li>)}
                  {result.clashes.map((c) => <li key={`${c[0]}${c[1]}`}>Clash: {c[0]} and {c[1]}</li>)}
                  {result.misfits.map((m) => <li key={m}>Doesn't fit: {m}</li>)}
                  {result.notes.map((n) => <li key={n}>{n}</li>)}
                </ul>
              </section>
            </div>
          )}
        </>
      )}
    </div>
  );
}
