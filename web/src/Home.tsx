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
        <h1>Bring a drawing</h1>
        <p className="lead">Drop a section drawing or a photo of a detail. Muse builds every part in Blender and checks its own work against your drawing.</p>
        <label className={`drop${over ? " over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}>
          <strong>Drop a drawing here</strong>
          <span>A PDF, PNG or JPG. One detail at a time.</span>
          <span className="solid">Choose a file<span className="square">{ICONS.upload}</span></span>
          <input id="studioFile" type="file" accept="application/pdf,image/png,image/jpeg" onChange={pick} />
        </label>
        <label className="build-method"><input type="checkbox" checked={parallel} onChange={(e) => setParallel(e.target.checked)} /> Try the parallel build (experimental)</label>
        {parallel && <p className="quiet method-note">Builds groups of parts together and uses suitable parts from your library, then checks the assembly against the drawing.</p>}
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
