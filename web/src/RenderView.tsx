import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import { Viewport3D } from "./viewport";
import type { ViewPreset } from "./viewport";
import type { Scene } from "./types";

declare global {
  interface Window {
    __ready?: { version_id: string; manifest_sha: string } | null;
    __renderError?: string | null;
    __renderSnapshot?: (view: ViewPreset, tags: boolean) => string;
  }
}

export default function RenderView({ versionId }: { versionId: string }): JSX.Element {
  const root = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let viewport: Viewport3D | null = null;
    window.__ready = null;
    window.__renderError = null;
    delete window.__renderSnapshot;
    void (async () => {
      const response = await fetch(`/api/versions/${encodeURIComponent(versionId)}/scene`, { signal: controller.signal });
      if (!response.ok) throw new Error(`Scene load failed (${response.status})`);
      const scene = await response.json() as Scene & { version_id: string; manifest_sha: string };
      if (!scene.solids.some(s => s.tri_count > 0)) throw new Error("Scene contains no geometry");
      if (!scene.manifest_sha) throw new Error("Build and verify before rendering");
      await document.fonts.ready;
      if (controller.signal.aborted || !root.current) return;
      viewport = new Viewport3D(root.current, () => {});
      viewport.loadScene(scene, { intro: false });
      viewport.skipIntro();
      viewport.setCutaway(false);
      viewport.setLabels(false);
      viewport.isolate(null);
      viewport.setState({ visible: Object.fromEntries(scene.solids.map(s => [s.id, true])),
                          seqOn: false, seqIdx: scene.install_order.length - 1, explode: 0, clipOn: false, clipZ: 0 });
      viewport.setView("iso", 0);
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      if (controller.signal.aborted) return;
      window.__renderSnapshot = (view, tags) => viewport!.snapshot(view, 1280, tags);
      window.__ready = { version_id: scene.version_id, manifest_sha: scene.manifest_sha };
    })().catch((failure: unknown) => {
      if (controller.signal.aborted) return;
      const message = failure instanceof Error ? failure.message : String(failure);
      window.__renderError = message;
      setError(message);
    });
    return () => {
      controller.abort();
      window.__ready = null;
      delete window.__renderSnapshot;
      viewport?.dispose();
    };
  }, [versionId]);
  return <div id="gl" ref={root} style={{ position: "fixed", inset: 0, width: "100vw", height: "100vh" }}>
    {error && <div role="alert">{error}</div>}
  </div>;
}
