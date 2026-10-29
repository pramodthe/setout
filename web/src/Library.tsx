/* Library front page: browse details by situation (sector > area > element)
   or by component (where a manufacturer's product would sit). */

import { useEffect, useMemo, useState } from "react";
import type { JSX } from "react";
import { api } from "./api";
import type { ComponentGroup, LibraryEntry } from "./types";

const human = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

function Card({ d, part }: { d: LibraryEntry; part?: string }): JSX.Element {
  const [thumb, setThumb] = useState(true);
  const href = `#/d/${encodeURIComponent(d.detail_id)}` + (part ? `?part=${encodeURIComponent(part)}` : "");
  return (
    <a className="card" href={href} data-detail={d.detail_id}>
      <div className="thumb">
        {thumb ? (
          <img src={api.previewUrl(d.detail_id)} alt="" loading="lazy" onError={() => setThumb(false)} />
        ) : (
          <span>{d.kind}</span>
        )}
      </div>
      <div className="body">
        <div className="path">{d.situation.join(" › ")}</div>
        <div className="title">{d.title}</div>
        <div className="meta">
          <span className={d.made_by === "ai" && d.review !== "reviewed" ? "badge b-assump" : "badge b-draw"}>
            {d.made_by === "ai" ? (d.review === "reviewed" ? "AI · reviewed" : "AI · draft") : "human transcription"}
          </span>
          {d.generating && <span className="badge b-gen" title="An AI run is working on this detail">generating…</span>}
          {d.located > 0 && <span title="parts located on the drawing by SAM 3.1">◎ {d.located}</span>}
          <span>{d.parts} parts · {d.drawing_values} drawing values · {d.labels} labels</span>
        </div>
        {part && <div className="meta">shows <b>{human(part)}</b></div>}
      </div>
    </a>
  );
}

export default function Library(): JSX.Element {
  const [details, setDetails] = useState<LibraryEntry[]>([]);
  const [components, setComponents] = useState<ComponentGroup[]>([]);
  const [tab, setTab] = useState<"situation" | "component">("situation");
  const [sector, setSector] = useState<string>("All");
  const [comp, setComp] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [pub, setPub] = useState(false);

  useEffect(() => {
    api
      .aiStatus()
      .then((s) => setPub(s.public))
      .catch(() => setPub(false));
    api
      .library()
      .then((b) => {
        setDetails(b.details);
        setComponents(b.components);
      })
      .catch((e: Error) => setErr(e.message));
  }, []);

  const match = (d: LibraryEntry) => {
    if (!q.trim()) return true;
    const hay = [d.title, ...d.situation, ...d.components.map((c) => `${c.type} ${c.label || ""}`)]
      .join(" ")
      .toLowerCase();
    return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
  };
  const shown = details.filter(match);
  const sectors = ["All", ...Array.from(new Set(details.map((d) => d.situation[0])))];
  const groups = useMemo(() => {
    const g = new Map<string, LibraryEntry[]>();
    for (const d of shown.filter((d) => sector === "All" || d.situation[0] === sector)) {
      const key = `${d.situation[0]} › ${d.situation[1]}`;
      g.set(key, [...(g.get(key) || []), d]);
    }
    return [...g.entries()];
  }, [shown, sector]);
  const byId = new Map(details.map((d) => [d.detail_id, d]));
  const compShown = components.filter((c) => !q.trim() || c.type.includes(q.toLowerCase().trim()));
  const sel = components.find((c) => c.type === comp) || null;

  return (
    <div className="lib">
      <header className="libhead">
        <div>
          <h1>Setout</h1>
          <div className="sub">Installation playground: every detail generated from its source, every value traceable</div>
        </div>
        <input
          className="search"
          placeholder="Search details, parts, labels…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {!pub && (
          <a className="btn primary" href="#/workbench">
            + Add from PDF
          </a>
        )}
      </header>
      <nav className="tabs">
        <button className={tab === "situation" ? "on" : ""} onClick={() => setTab("situation")}>
          By situation
        </button>
        <button className={tab === "component" ? "on" : ""} onClick={() => setTab("component")}>
          By component / product
        </button>
        <span className="count">{details.length} details</span>
      </nav>
      {err && <div className="liberr">Could not load the library: {err}</div>}
      {tab === "situation" && (
        <>
          <div className="chips">
            {sectors.map((s) => (
              <button key={s} className={s === sector ? "chip on" : "chip"} onClick={() => setSector(s)}>
                {s}
              </button>
            ))}
          </div>
          {groups.map(([area, ds]) => (
            <section key={area}>
              <h2>{area}</h2>
              <div className="grid">
                {ds.map((d) => (
                  <Card key={d.detail_id} d={d} />
                ))}
              </div>
            </section>
          ))}
          {!groups.length && details.length > 0 && <p className="empty">Nothing matches “{q}”.</p>}
        </>
      )}
      {tab === "component" && (
        <>
          <p className="hint">
            Pick a component to see every detail where it is installed. Product references are not
            assigned yet (no manufacturer system selected), so parts are grouped by type.
          </p>
          <div className="chips">
            {compShown.map((c) => (
              <button
                key={c.type}
                className={c.type === comp ? "chip on" : "chip"}
                onClick={() => setComp(c.type)}
              >
                {human(c.type)} <span className="n">{c.details.length}</span>
              </button>
            ))}
          </div>
          {sel && (
            <section>
              <h2>{human(sel.type)} — installed in {sel.details.length} detail(s)</h2>
              <div className="grid">
                {sel.details.map((x) => {
                  const d = byId.get(x.detail_id);
                  return d ? <Card key={x.detail_id} d={d} part={x.part_ids[0]} /> : null;
                })}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
