import { StrictMode, useEffect, useState } from "react";
import type { JSX } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import Studio from "./Studio.tsx";

/* Hash routes: #/ home · #/studio/<id> a scene */
function Root(): JSX.Element {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const follow = () => setHash(location.hash);
    addEventListener("hashchange", follow);
    return () => removeEventListener("hashchange", follow);
  }, []);
  const scene = hash.match(/^#\/studio\/([^?]+)/);
  return scene ? <Studio key={scene[1]} sceneId={decodeURIComponent(scene[1])} /> : <Studio key="home" sceneId={null} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
