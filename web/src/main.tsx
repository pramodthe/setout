import { StrictMode, useEffect, useState } from "react";
import type { JSX } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import DetailView from "./DetailView.tsx";
import Library from "./Library.tsx";
import RenderView from "./RenderView.tsx";
import JobPanel from "./JobPanel.tsx";

/* Hash routes: #/ library · #/d/<id>[?part=<id>] detail · #/workbench add-from-PDF */
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
