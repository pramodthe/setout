import { StrictMode, useEffect, useState } from "react";
import type { JSX, ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import "./studio.css";
import AppBar from "./AppBar.tsx";
import App from "./App.tsx";
import DetailView from "./DetailView.tsx";
import Studio from "./Studio.tsx";
import RenderView from "./RenderView.tsx";
import JobPanel from "./JobPanel.tsx";

/* Hash routes: #/ studio home · #/studio/<id> a scene · #/d/<id>[?part=<id>] verified detail · #/workbench add a verified detail */
function Page({ children }: { children: ReactNode }): JSX.Element {
  return <div className="pagewrap"><AppBar /><div className="pagebody">{children}</div></div>;
}

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
  if (job) return <Page><JobPanel jobId={decodeURIComponent(job[1])} /></Page>;
  if (hash.startsWith("#/workbench")) return <Page><App /></Page>;
  const studio = hash.match(/^#\/studio\/([^?]+)/);
  if (studio) return <Studio key={studio[1]} sceneId={decodeURIComponent(studio[1])} />;
  const m = hash.match(/^#\/d\/([^?]+)(?:\?part=(.+))?/);
  if (m) {
    const id = decodeURIComponent(m[1]);
    return <Page><DetailView key={id} detailId={id} part={m[2] ? decodeURIComponent(m[2]) : null} /></Page>;
  }
  return <Studio key="new" sceneId={null} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
