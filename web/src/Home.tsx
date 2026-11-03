/* The start page: a place to drop a drawing, and the details built so far. */

import { useState } from "react";
import type { ChangeEvent, DragEvent, JSX } from "react";
import { ICONS } from "./icons";
import type { Card } from "./scene";

export default function Home({ scenes, onFile, onDelete, error }: { scenes: Card[]; onFile: (file: File, method: "serial" | "hybrid") => void; onDelete: (card: Card) => void; error: string | null }): JSX.Element {
  const [over, setOver] = useState(false);
  const [parallel, setParallel] = useState(false);
  const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); const file = e.dataTransfer.files[0]; if (file) onFile(file, parallel ? "hybrid" : "serial"); };
  const pick = (e: ChangeEvent<HTMLInputElement>) => { const file = e.target.files?.[0]; if (file) onFile(file, parallel ? "hybrid" : "serial"); };
  return (
    <main className="home">
      <div className="hero">
        <h1>Turn a section drawing into a 3D detail</h1>
        <p>Muse reads your drawing and builds every part in Blender, checking its own work as it goes.</p>
        <label className="build-method"><input type="checkbox" checked={parallel} onChange={(e) => setParallel(e.target.checked)} /> Try parallel build (experimental)</label>
        {parallel && <p>Builds component groups together and uses suitable parts from your library, then checks the assembly against the drawing.</p>}
        <label className={`drop${over ? " over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}>
          <strong>Drop a section drawing here</strong>
          <span>or click to choose a PDF, PNG or JPG</span>
          <input id="studioFile" type="file" accept="application/pdf,image/png,image/jpeg" onChange={pick} />
        </label>
        {error && <p className="warm" role="alert">{error}</p>}
      </div>
      {scenes.length > 0 && (
        <section className="gallery">
          <h2>Your details</h2>
          <div className="sc-grid">
            {scenes.map((s) => (
              <div key={s.id} className="sc-item">
              <a className="sc-card" href={`#/studio/${s.id}`}>
                <div className="thumb">{s.has_preview && <img src={`/api/studio/${s.id}/preview`} alt="" loading="lazy" />}</div>
                <div className="meta"><strong>{s.title}</strong><span>{s.state === "working" ? "Muse is building" : s.state === "failed" ? "Stopped" : `${s.parts} parts`}</span></div>
              </a>
              {s.state !== "working" && <button className="sc-delete" aria-label={`Delete ${s.title}`} title="Delete" onClick={() => onDelete(s)}>{ICONS.trash}</button>}
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
