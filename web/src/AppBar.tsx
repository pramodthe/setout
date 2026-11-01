/* The bar across every page, so the whole app reads as one thing. */

import type { JSX, ReactNode } from "react";

export default function AppBar({ right }: { right?: ReactNode }): JSX.Element {
  return (
    <header className="appbar">
      <a className="brand" href="#/"><i />Setout</a>
      <div className="topright">{right}</div>
    </header>
  );
}
