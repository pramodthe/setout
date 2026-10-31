export interface TreeNode {
  name: string;
  type: string;
  children?: TreeNode[];
  location?: [number, number, number];
  rotation_deg?: [number, number, number];
  dims?: Record<string, number>;
  material?: string;
}

export interface PartOverride {
  location?: [number, number, number];
  rotation_deg?: [number, number, number];
  dims?: Record<string, number>;
}

export type OverrideMap = Record<string, PartOverride>;

export interface History {
  past: OverrideMap[];
  present: OverrideMap;
  future: OverrideMap[];
}

export interface PatchOverride {
  name: string;
  location?: [number, number, number];
  rotation_deg?: [number, number, number];
  dims?: Record<string, number>;
}

export function yUpMetresToZUpMm(x: number, y: number, z: number): [number, number, number] {
  return [round(x * 1000), round(-z * 1000), round(y * 1000)];
}

/** Same rule as the Blender harness: a changed dimension that matches an axis span scales that axis. */
export function axisScaleFactors(
  spansMm: number[],
  oldDims: Record<string, number>,
  newDims: Record<string, number>,
): [number, number, number] {
  const scale: [number, number, number] = [1, 1, 1];
  for (const [key, raw] of Object.entries(newDims)) {
    if (!(key in oldDims)) continue;
    const previous = oldDims[key];
    if (Math.abs(previous) < 0.05 || Math.abs(raw - previous) < 0.05) continue;
    const tolerance = Math.max(0.75, 0.02 * Math.abs(previous));
    const index = spansMm.findIndex((span) => Math.abs(span - previous) <= tolerance);
    if (index >= 0) scale[index] = raw / previous;
  }
  return scale;
}

export function meshesOf(node: TreeNode | null): TreeNode[] {
  if (!node) return [];
  const found: TreeNode[] = [];
  const visit = (item: TreeNode) => {
    if (item.type === "mesh") found.push(item);
    for (const child of item.children || []) visit(child);
  };
  visit(node);
  return found;
}

export function meshByName(node: TreeNode | null, name: string | null): TreeNode | null {
  if (!name) return null;
  return meshesOf(node).find((mesh) => mesh.name === name) || null;
}

export function commitHistory(history: History, present: OverrideMap): History {
  return { past: [...history.past, history.present], present, future: [] };
}

export function undoHistory(history: History): History {
  if (!history.past.length) return history;
  const present = history.past[history.past.length - 1];
  return {
    past: history.past.slice(0, -1),
    present,
    future: [history.present, ...history.future],
  };
}

export function redoHistory(history: History): History {
  if (!history.future.length) return history;
  const [present, ...future] = history.future;
  return { past: [...history.past, history.present], present, future };
}

export type HistoryAction =
  | { type: "commit"; present: OverrideMap }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "reset" };

export function historyReducer(state: History, action: HistoryAction): History {
  if (action.type === "reset") return { past: [], present: {}, future: [] };
  if (action.type === "commit") return commitHistory(state, action.present);
  if (action.type === "undo") return undoHistory(state);
  return redoHistory(state);
}

export function diffOverrides(base: Record<string, TreeNode>, pending: OverrideMap): { overrides: PatchOverride[] } {
  const overrides: PatchOverride[] = [];
  for (const [name, edit] of Object.entries(pending)) {
    const mesh = base[name];
    if (!mesh) continue;
    const patch: PatchOverride = { name };
    if (edit.location && !close(edit.location, mesh.location || [0, 0, 0], 0.05)) patch.location = edit.location;
    if (edit.rotation_deg && !close(edit.rotation_deg, mesh.rotation_deg || [0, 0, 0], 0.05)) {
      patch.rotation_deg = edit.rotation_deg;
    }
    if (edit.dims) {
      const dims: Record<string, number> = {};
      for (const [key, value] of Object.entries(edit.dims)) {
        if (mesh.dims?.[key] !== value) dims[key] = value;
      }
      if (Object.keys(dims).length) patch.dims = dims;
    }
    if (patch.location || patch.rotation_deg || patch.dims) overrides.push(patch);
  }
  return { overrides };
}

function close(a: number[], b: number[], tol: number): boolean {
  return a.length === b.length && a.every((value, index) => Math.abs(value - b[index]) <= tol);
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}
