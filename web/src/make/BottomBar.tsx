import type { JSX } from "react";

export type RenderQuality = "draft" | "high";

export default function BottomBar({
  quality, onQuality, onRefresh, onRender, busy, rendering, status,
  glbUrl, treeUrl, previewUrl,
}: {
  quality: RenderQuality;
  onQuality: (quality: RenderQuality) => void;
  onRefresh: () => void;
  onRender: () => void;
  busy: boolean;
  rendering: boolean;
  status: string | null;
  glbUrl: string | null;
  treeUrl: string | null;
  previewUrl: string | null;
}): JSX.Element {
  return (
    <footer className="make-bottom" data-make="bottom">
      <label className="make-chip">
        Quality
        <select
          data-make="quality"
          value={quality}
          onChange={(event) => onQuality(event.target.value === "high" ? "high" : "draft")}
        >
          <option value="draft">Draft</option>
          <option value="high">High</option>
        </select>
      </label>
      <button type="button" data-make="refresh" onClick={onRefresh}>Refresh</button>
      <a data-make="download-glb" href={glbUrl || undefined} download="scene.glb" aria-disabled={!glbUrl}>scene.glb</a>
      <a data-make="download-tree" href={treeUrl || undefined} download="tree.json" aria-disabled={!treeUrl}>tree.json</a>
      <a data-make="download-preview" href={previewUrl || undefined} download="preview.png" aria-disabled={!previewUrl}>preview.png</a>
      <button type="button" className="primary" data-make="render" disabled={busy || !glbUrl} onClick={onRender}>
        {rendering ? "Rendering…" : "Use Blender"}
      </button>
      {status && <span className="make-muted" data-make="render-status">{status}</span>}
    </footer>
  );
}
