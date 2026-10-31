import type { JSX } from "react";
import type { PartOverride, TreeNode } from "./edits";
import NumberField from "./NumberField";

export default function PropertiesPanel({ mesh, pending, onDim, onLocation, onRotation, onApply, canApply, busy }: {
  mesh: TreeNode | null;
  pending: PartOverride | null;
  onDim: (key: string, value: number) => void;
  onLocation: (index: number, value: number) => void;
  onRotation: (index: number, value: number) => void;
  onApply: () => void;
  canApply: boolean;
  busy: boolean;
}): JSX.Element {
  if (!mesh) {
    return (
      <div className="make-props">
        <h2>Properties</h2>
        <p className="make-muted">Select a part in the tree or the view.</p>
      </div>
    );
  }
  const location = pending?.location || mesh.location || [0, 0, 0];
  const rotation = pending?.rotation_deg || mesh.rotation_deg || [0, 0, 0];
  const dims = { ...(mesh.dims || {}), ...(pending?.dims || {}) };
  return (
    <div className="make-props" data-make="properties">
      <div className="make-props-head">
        <h2>Properties</h2>
        <button type="button" className="primary" data-make="apply" disabled={!canApply || busy} onClick={onApply}>
          Apply
        </button>
      </div>
      <p className="make-name" data-make="part-name">{mesh.name}</p>
      <p className="make-muted">Material {mesh.material || "—"}</p>
      <h2>Dimensions (mm)</h2>
      {Object.entries(dims).map(([key, value]) => (
        <label key={key}>
          {key}
          <NumberField dim={key} value={Number.isFinite(value) ? value : 0} onCommit={(next) => onDim(key, next)} />
        </label>
      ))}
      {!Object.keys(dims).length && <p className="make-muted">No dimensions on this part.</p>}
      <h2>Location (mm)</h2>
      {["X", "Y", "Z"].map((axis, index) => (
        <label key={axis}>
          {axis}
          <NumberField loc={axis} value={location[index]} onCommit={(next) => onLocation(index, next)} />
        </label>
      ))}
      <h2>Rotation (deg)</h2>
      {["X", "Y", "Z"].map((axis, index) => (
        <label key={axis}>
          {axis}
          <NumberField rot={axis} value={rotation[index]} onCommit={(next) => onRotation(index, next)} />
        </label>
      ))}
    </div>
  );
}
