/* The bar across Studio pages. Its Setout link returns to the landing story. */

import type { JSX, ReactNode } from "react";

export default function AppBar({ right }: { right?: ReactNode }): JSX.Element {
  return (
    <header className="appbar">
      <a className="brand" href="#/"><i />Setout</a>
      <div className="topright">{right}</div>
    </header>
  );
}
