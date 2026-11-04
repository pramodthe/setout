/* The bar across Studio pages. Its Setout link returns to the landing story; a scene puts its title and tools in it too. */

import type { JSX, ReactNode } from "react";
import Mark from "./Mark";

export default function AppBar({ children, right }: { children?: ReactNode; right?: ReactNode }): JSX.Element {
  return (
    <header className="appbar">
      <a className="brand" href="#/" aria-label="Setout, the story"><Mark />Setout</a>
      {children}
      <div className="topright">{right}</div>
    </header>
  );
}
