/* The panels under the scene tree: the selected part, and what the checker found. */

import type { JSX } from "react";
import { mm, pretty } from "./scene";
import type { Checks, Measured, Row } from "./scene";

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

const VERB: Record<string, string> = { on: "sits on", against: "stands against", through: "passes through", wraps: "wraps", covers: "covers", apart: "stays apart from", left_of: "is left of" };
const STATE: Record<string, { mark: string; word: string; rank: number }> = {
  fails: { mark: "!", word: "doesn't hold", rank: 0 }, unknown: { mark: "?", word: "couldn't be measured", rank: 1 }, holds: { mark: "✓", word: "holds", rank: 2 },
};
const LAST_WORD = 6;  /* parts named in a line before it says how many more */

type Marking = { marked: string | null; onMark: (key: string, names: string[]) => void; onStrike: (index: number, struck: boolean) => void; onFix: (text: string) => void };

function Measurements({ measured, marked, onMark, onStrike, onFix }: { measured: Measured } & Marking): JSX.Element | null {
  const rows = [...(measured.rows ?? [])].sort((a, b) => Number(!!a.struck) - Number(!!b.struck) || STATE[a.state].rank - STATE[b.state].rank);
  const loose = measured.loose ?? [];
  const overlaps = measured.overlaps ?? [];
  if (!rows.length && !loose.length && !overlaps.length) return null;
  const counted = rows.filter((r) => !r.struck);
  const line = (key: string, names: string[], text: string) => <button className={`line${marked === key ? " on" : ""}`} aria-pressed={marked === key} onClick={() => onMark(key, names)}>{text}</button>;
  const fix = (text: string) => <button className="strike" title="Send this to Muse to change" onClick={() => onFix(text)}>Ask Muse to fix</button>;
  return (
    <div className="measured">
      <h3>Measured{counted.length > 0 && `: ${counted.filter((r) => r.state === "holds").length} of ${counted.length} relations hold`}</h3>
      <p className="note">Read from the model's geometry: what touches, overlaps and sits where, and never a size. Muse's reading of your drawing can be wrong too, so this goes to you and not to Muse. Click a line to see its parts.</p>
      <ul>
        {rows.map((row) => (
          <li key={row.index} className={row.struck ? "struck" : row.state} title={STATE[row.state].word}>
            <b aria-label={STATE[row.state].word}>{STATE[row.state].mark}</b>
            <span>
              {line(`relation ${row.index}`, row.objects, `${row.a} ${VERB[row.relation]} ${row.b}`)}
              <small>
                {row.state === "fails" && !row.struck && fix(`Measured in the model, this doesn't hold: ${row.a} ${VERB[row.relation]} ${row.b}.`)}
                <button className="strike" title={row.struck ? "Check this again next time" : "Muse misread this in your drawing"} onClick={() => onStrike(row.index, !row.struck)}>{row.struck ? "Put back" : "Misread"}</button>
              </small>
            </span>
          </li>
        ))}
        {loose.map((group, k) => <li key={`loose ${k}`} className="fails" title="touches nothing else"><b aria-label="loose">!</b><span>{line(`loose ${k}`, group, `${group.slice(0, LAST_WORD).join(", ")}${group.length > LAST_WORD ? ` and ${group.length - LAST_WORD} more` : ""} touches nothing else`)}<small>{fix(`Measured in the model, ${group.join(", ")} touches nothing else. Make it bear on what carries it.`)}</small></span></li>)}
        {overlaps.map((o, k) => <li key={`overlap ${k}`} className="fails" title="the two share space"><b aria-label="overlap">!</b><span>{line(`overlap ${k}`, [o.a, o.b], `${o.a} and ${o.b} share space`)}<small>{fix(`Measured in the model, ${o.a} and ${o.b} share space. Separate them, unless one really passes through the other.`)}</small></span></li>)}
      </ul>
    </div>
  );
}

export function Checker({ checks, measuring, marked, onMark, onStrike, onFix }: { checks: Checks; measuring: boolean } & Marking): JSX.Element {
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
      {measuring && <p className="note measuring" role="status">Measuring how the parts meet. This takes a few minutes, and you can carry on.</p>}
      {checks.measured && <Measurements measured={checks.measured} marked={marked} onMark={onMark} onStrike={onStrike} onFix={onFix} />}
    </section>
  );
}
