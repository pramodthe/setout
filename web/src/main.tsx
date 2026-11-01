import { StrictMode, useEffect, useState } from "react";
import type { JSX } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import DetailView from "./DetailView.tsx";
import Library from "./Library.tsx";
import AssemblyView from "./AssemblyView.tsx";
import DraftView from "./DraftView.tsx";
import RenderView from "./RenderView.tsx";
import JobPanel from "./JobPanel.tsx";

/* Hash routes: #/ library · #/d/<id>[?part=<id>] detail · #/workbench add-from-PDF · #/assembly[/<id>] planned 3D from a section */
function Root(): JSX.Element {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const f = () => setHash(location.hash);
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  const render = hash.match(/^#\/render\/([^?]+)/);
  if (render) return <RenderView key={render[1]} versionId={decodeURIComponent(render[1])} />;
  const job = hash.match(/^#\/jobs\/([^?]+)/);
  if (job) return <><header><a href="#/">← Library</a></header><JobPanel jobId={decodeURIComponent(job[1])} /></>;
  if (hash.startsWith("#/workbench")) return <App />;
  const assembly = hash.match(/^#\/assembly(?:\/([^?]+))?/);
  if (assembly) return <AssemblyView key={assembly[1] || "new"} assemblyId={assembly[1] ? decodeURIComponent(assembly[1]) : null} />;
  const draft = hash.match(/^#\/draft(?:\/([^?]+))?/);
  if (draft) return <DraftView key={draft[1] || "new"} draftId={draft[1] ? decodeURIComponent(draft[1]) : null} />;
  const m = hash.match(/^#\/d\/([^?]+)(?:\?part=(.+))?/);
  if (m) {
    const id = decodeURIComponent(m[1]);
    return <DetailView key={id} detailId={id} part={m[2] ? decodeURIComponent(m[2]) : null} />;
  }
  return <Library />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
