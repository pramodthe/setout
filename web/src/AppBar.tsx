/* The bar across every page, so the whole app reads as one thing. */

import type { JSX, ReactNode } from "react";

export default function AppBar({ page, children, right }: { page?: "studio" | "library"; children?: ReactNode; right?: ReactNode }): JSX.Element {
  return (
    <header className="appbar">
      <a className="brand" href="#/"><i />Setout</a>
      <nav className="mainnav" aria-label="Sections">
        <a className={page === "studio" ? "on" : ""} aria-current={page === "studio" ? "page" : undefined} href="#/">Studio</a>
        <a className={page === "library" ? "on" : ""} aria-current={page === "library" ? "page" : undefined} href="#/library">Library</a>
      </nav>
      {children}
      <div className="topright">{right}</div>
    </header>
  );
}
