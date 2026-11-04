/* The selected part’s properties below the scene tree. */

import type { JSX } from "react";
import { mm, pretty } from "./scene";
import type { Row } from "./scene";

export function Props({ row, intent }: { row: Row; intent?: string }): JSX.Element {
  const size = row.to.map((v, k) => v - row.from[k]);
  const centre = row.to.map((v, k) => (v + row.from[k]) / 2);
  const triple = (values: number[]) => <div className="triple">{values.map((v, k) => <div key={k}><b>{"XYZ"[k]}</b>{mm(v)}</div>)}</div>;
  return (
    <section className="props" aria-label="Selected part">
      <h3>{pretty(row.part)}</h3>
      <div className="field"><label>Position, mm</label>{triple(centre)}</div>
      <div className="field"><label>Size, mm</label>{triple(size)}</div>
      {row.material && <div className="field"><label>Material</label>{pretty(row.material)}</div>}
      {intent && <div className="field"><label>Built in the step</label>{intent}</div>}
      <p className="note">Size chosen by Muse. The drawing isn't to scale, and nothing here is verified.</p>
    </section>
  );
}
