/* A section drawing to a local 3D draft. Apart from verified details: nothing here is checked or
   published, and the page says so. Parts the drawing doesn't show render ghosted, as inferred. */

import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, JSX } from "react";
import { Viewport3D } from "./viewport";
import type { Scene } from "./types";

type Draft = { id: string; state: string; step?: string; error?: string; scene?: Scene; regions?: number;
               from_drawing?: number; inferred?: number; tokens?: number };

export default function DraftView({ draftId }: { draftId: string | null }): JSX.Element {
  const root = useRef<HTMLDivElement>(null);
  const viewport = useRef<Viewport3D | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [explode, setExplode] = useState(0);

  useEffect(() => {
    if (!draftId) return;
    let live = true;
    const look = async () => {
      const response = await fetch(`/api/section-drafts/${encodeURIComponent(draftId)}`);
      if (!response.ok) throw new Error("This draft could not be found");
      const found = (await response.json()) as Draft;
      if (!live) return;
      setDraft(found);
      if (found.state === "running") setTimeout(() => void look().catch((e: Error) => setError(e.message)), 2000);
    };
    void look().catch((e: Error) => setError(e.message));
    return () => { live = false; };
  }, [draftId]);

  const scene = draft?.scene;
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

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const sent = await fetch("/api/documents", { method: "POST", body });
      if (!sent.ok) throw new Error((await sent.text()).slice(0, 200));
      const document_ = (await sent.json()) as { id: string };
      const record = (await (await fetch(`/api/documents/${document_.id}`)).json()) as { crops: { crop_id: string }[] };
      const started = await fetch("/api/section-drafts", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_id: document_.id, crop_id: record.crops[0].crop_id }) });
      if (!started.ok) throw new Error((await started.text()).slice(0, 200));
      location.hash = `#/draft/${((await started.json()) as { id: string }).id}`;
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

  return (
    <div className="draft">
      <header className="dvhead">
        <a href="#/" className="back">← Library</a>
        <div className="ttl">
          <div className="path">Section draft · not verified · can't be published</div>
          <h1>{scene?.title || "New draft from a section"}</h1>
        </div>
        {draft?.state === "done" && (
          <span className="vsum" id="draftCounts">
            {draft.from_drawing} from the drawing · {draft.inferred} inferred (ghosted)
          </span>
        )}
      </header>
      {!draftId && <p className="draftnote"><label>Choose a one-page PDF of a section <input id="draftPdf" type="file" accept="application/pdf" onChange={(e) => void upload(e)} /></label></p>}
      {draft?.state === "running" && <p className="draftnote" role="status">{draft.step}…</p>}
      {draft?.state === "failed" && <p className="liberr" role="alert">{draft.error}</p>}
      {error && <p className="liberr" role="alert">{error}</p>}
      {scene && (
        <p className="draftnote">
          Shapes come from the drawing, and sizes are assumed. Ghosted parts aren't in the drawing: a model listed them.{" "}
          <label>explode <input id="draftExplode" type="range" min={0} max={1} step={0.02} value={explode} onChange={(e) => move(Number(e.target.value))} /></label>
        </p>
      )}
      <div ref={root} id="gl" style={{ position: "relative", width: "100%", height: "78vh" }} />
    </div>
  );
}
