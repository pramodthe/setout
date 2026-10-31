import type { JSX } from "react";

export interface MakeTab {
  id: string;
  title: string;
}

export default function TabBar({ tabs, active, onNew }: {
  tabs: MakeTab[];
  active: string | null;
  onNew: () => void;
}): JSX.Element {
  return (
    <div className="make-tabs">
      <a className="back" href="#/">← Library</a>
      {tabs.map((tab) => (
        <a key={tab.id} className={tab.id === active ? "make-tab on" : "make-tab"} href={`#/make/${encodeURIComponent(tab.id)}`}>
          {tab.title || "Untitled"}
        </a>
      ))}
      <button type="button" className="make-tab" onClick={onNew} data-make="new-tab">+</button>
    </div>
  );
}
