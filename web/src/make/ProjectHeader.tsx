import type { JSX } from "react";
import type { MakeModel, MakeVersion } from "./makeApi";

export default function ProjectHeader({ title, versions, selected, onSelect, models, profile, onProfile }: {
  title: string;
  versions: MakeVersion[];
  selected: number | null;
  onSelect: (n: number) => void;
  models: MakeModel[];
  profile: string;
  onProfile: (id: string) => void;
}): JSX.Element {
  const done = versions.filter((version) => version.status === "done");
  return (
    <header className="make-head">
      <h1 data-make="title">{title || "Untitled"}</h1>
      <label className="make-chip">
        Version
        <select
          data-make="version"
          value={selected ?? ""}
          disabled={!done.length}
          onChange={(event) => onSelect(Number(event.target.value))}
        >
          {!done.length && <option value="">none yet</option>}
          {done.map((version) => (
            <option key={version.n} value={version.n}>v{version.n} · {version.source}</option>
          ))}
        </select>
      </label>
      <label className="make-chip">
        Model
        <select data-make="model" value={profile} onChange={(event) => onProfile(event.target.value)}>
          {models.map((model) => (
            <option key={model.id} value={model.id} disabled={!model.ready}>
              {model.label}{model.ready ? "" : " (not configured)"}
            </option>
          ))}
          {!models.length && <option value="">Model</option>}
        </select>
      </label>
    </header>
  );
}
