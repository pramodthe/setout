/* Deleting can't be undone, so it asks first. */

import { useEffect, useRef } from "react";
import type { JSX } from "react";

export default function ConfirmDelete({ title, onCancel, onConfirm }: { title: string; onCancel: () => void; onConfirm: () => void }): JSX.Element {
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancel.current?.focus();
    const leave = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    addEventListener("keydown", leave);
    return () => removeEventListener("keydown", leave);
  }, [onCancel]);
  return (
    <div className="scrim" onClick={onCancel}>
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="deleteTitle" aria-describedby="deleteNote" onClick={(e) => e.stopPropagation()}>
        <h2 id="deleteTitle">Delete "{title}"?</h2>
        <p id="deleteNote">The model, the drawing and its build history go too. This can't be undone.</p>
        <div className="dialogbuttons">
          <button ref={cancel} className="ghost" onClick={onCancel}>Cancel</button>
          <button id="confirmDelete" className="ghost danger" onClick={onConfirm}>Delete</button>
        </div>
      </div>
    </div>
  );
}
