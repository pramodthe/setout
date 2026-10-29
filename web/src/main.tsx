import { StrictMode, useEffect, useState } from "react";
import type { JSX } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import DetailView from "./DetailView.tsx";
import Library from "./Library.tsx";

/* Hash routes: #/ library · #/d/<id>[?part=<id>] detail · #/workbench add-from-PDF */
function Root(): JSX.Element {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const f = () => setHash(location.hash);
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
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
