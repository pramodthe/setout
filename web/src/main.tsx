import { lazy, StrictMode, Suspense, useEffect, useState } from "react";
import type { JSX } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
const Studio = lazy(() => import("./Studio.tsx"));
const Anatomy = lazy(() => import("./Anatomy.tsx"));

/* Hash routes: #/ landing · #/app studio home · #/studio/<id> a scene */
export default function Root(): JSX.Element {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const follow = () => {
      if (location.hash === "#/anatomy" || location.hash.startsWith("#/anatomy?")) {
        history.replaceState(null, "", `${location.pathname}${location.search}#/`);
      }
      setHash(location.hash);
    };
    follow();
    addEventListener("hashchange", follow);
    return () => removeEventListener("hashchange", follow);
  }, []);
  const scene = hash.match(/^#\/studio\/([^?]+)/);
  if (scene) return <Suspense fallback={<div style={{ minHeight: "100vh", background: "#e4e4e2" }} />}><Studio key={scene[1]} sceneId={decodeURIComponent(scene[1])} /></Suspense>;
  if (hash === "#/app" || hash.startsWith("#/app?")) return <Suspense fallback={<div style={{ minHeight: "100vh", background: "#e4e4e2" }} />}><Studio key="home" sceneId={null} /></Suspense>;
  return <Suspense fallback={<div style={{ minHeight: "100vh", background: "#c7c7c7" }} />}><Anatomy /></Suspense>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
