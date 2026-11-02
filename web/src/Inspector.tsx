/* The panels under the scene tree: the selected part, and what the checker found. */

import type { JSX } from "react";
import { mm, pretty } from "./scene";
import type { Checks, Row } from "./scene";

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

const MARK: Record<string, string> = { right: "✓", wrong: "!", missing: "✕", cant_tell: "?" };
const WORD: Record<string, string> = { right: "right", wrong: "wrong", missing: "missing", cant_tell: "couldn't tell" };

export function Checker({ checks }: { checks: Checks }): JSX.Element {
  const last = checks.last!;
  const rows = [...last.rows].sort((a, b) => Number(a.state === "right") - Number(b.state === "right"));
  return (
    <section className="props checker" aria-label="Checker">
      <h3>Checker: {last.right} of {last.total} right</h3>
      <p className="note">An independent Muse call compared the model with your drawing from five sides{checks.rounds.length > 1 ? `, over ${checks.rounds.length} rounds: ${checks.rounds.map((r) => `${r.right}/${r.total}`).join(" → ")}` : ""}. It can be wrong too.</p>
      <ul>
        {rows.map((tick) => (
          <li key={tick.id} className={tick.state} title={WORD[tick.state]}>
            <b aria-label={WORD[tick.state]}>{MARK[tick.state]}</b><span>{tick.what}{tick.note && <small>{tick.note}</small>}</span>
          </li>
        ))}
      </ul>
      {last.not_in_source.length > 0 && <p className="note warm">Not in your drawing: {last.not_in_source.join("; ")}</p>}
    </section>
  );
}
