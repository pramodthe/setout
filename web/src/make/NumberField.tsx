import { useEffect, useState } from "react";
import type { JSX } from "react";

export default function NumberField({ value, onCommit, dim, loc, rot }: {
  value: number;
  onCommit: (value: number) => void;
  dim?: string;
  loc?: string;
  rot?: string;
}): JSX.Element {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  function commit() {
    const next = Number(draft);
    if (Number.isFinite(next) && next !== value) onCommit(next);
  }
  return (
    <input
      data-dim={dim}
      data-loc={loc}
      data-rot={rot}
      type="number"
      step="0.1"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") (event.target as HTMLInputElement).blur();
      }}
    />
  );
}
