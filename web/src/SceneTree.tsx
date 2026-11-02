/* The parts of a scene by group, each one selectable and hideable. */

import { useMemo, useState } from "react";
import type { JSX } from "react";
import { ICONS } from "./icons";
import { pretty } from "./scene";
import type { Row } from "./scene";

export default function SceneTree({ rows, selected, hidden, onSelect, onHide }: { rows: Row[]; selected: string | null; hidden: Set<string>; onSelect: (name: string) => void; onHide: (names: string[], hide: boolean) => void }): JSX.Element {
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const groups = useMemo(() => {
    const held = new Map<string, Map<string, Row[]>>();
    for (const row of rows) {
      const group = held.get(row.group) ?? new Map<string, Row[]>();
      group.set(row.part, [...(group.get(row.part) ?? []), row]);
      held.set(row.group, group);
    }
    return [...held.entries()];
  }, [rows]);
  const toggle = (key: string) => setClosed((held) => { const next = new Set(held); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  const eye = (names: string[], label: string) => {
    const off = names.every((n) => hidden.has(n));
    return <button className={`eye${off ? " off" : ""}`} aria-label={`${off ? "Show" : "Hide"} ${label}`} onClick={(e) => { e.stopPropagation(); onHide(names, !off); }}>{off ? ICONS.eyeOff : ICONS.eye}</button>;
  };
  return (
    <>
      {groups.map(([group, parts]) => {
        const all = [...parts.values()].flat().map((r) => r.name);
        const open = !closed.has(group);
        return (
          <div key={group}>
            <div className="row">
              <button className={`twist${open ? " open" : ""}`} aria-label={`${open ? "Close" : "Open"} ${pretty(group)}`} aria-expanded={open} onClick={() => toggle(group)}>{ICONS.chevron}</button>
              <button className="label" onClick={() => toggle(group)}>{ICONS.cube}<span>{pretty(group)}</span></button>
              <span className="count">{all.length}</span>{eye(all, pretty(group))}
            </div>
            {open && [...parts.entries()].map(([part, held]) => {
              const key = `${group}/${part}`;
              const many = held.length > 1;
              const expanded = many && !closed.has(key) === false;
              const names = held.map((r) => r.name);
              return (
                <div key={key} className="child">
                  <div className={`row${names.includes(selected ?? "") && !expanded ? " on" : ""}`}>
                    {many ? <button className={`twist${expanded ? " open" : ""}`} aria-label={`${expanded ? "Close" : "Open"} ${part}`} onClick={() => toggle(key)}>{ICONS.chevron}</button> : <span className="twist" />}
                    <button className="label" onClick={() => onSelect(held[0].name)}>{ICONS.cube}<span>{pretty(part)}</span></button>
                    {many && <span className="count">{held.length}</span>}{eye(names, pretty(part))}
                  </div>
                  {expanded && held.map((row) => (
                    <div key={row.name} className="child">
                      <div className={`row${row.name === selected ? " on" : ""}`}><span className="twist" /><button className="label" onClick={() => onSelect(row.name)}><span>{row.name}</span></button>{eye([row.name], row.name)}</div>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        );
      })}
    </>
  );
}
