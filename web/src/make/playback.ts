/** Install playback. One step per part, in build_order, dropping 50 mm while it fades in. */

export const DROP_MM = 50;
export const STEP_MS = 520;

export interface InstallStep {
  name: string;
  order: number;
}

export interface PartPose {
  opacity: number;
  dropMm: number;
}

export function installSteps(nodes: { name: string; build_order?: number }[]): InstallStep[] {
  return nodes
    .map((node) => ({
      name: node.name,
      order: Number.isFinite(node.build_order) ? Number(node.build_order) : 0,
    }))
    .sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));
}

export function easeOut(t: number): number {
  const clamped = Math.min(1, Math.max(0, t));
  return 1 - (1 - clamped) * (1 - clamped);
}

/** `progress` runs from 0 to `steps.length`. Step i is seated once progress reaches i + 1. */
export function posesAt(steps: InstallStep[], progress: number): Record<string, PartPose> {
  const poses: Record<string, PartPose> = {};
  steps.forEach((step, index) => {
    const t = easeOut(progress - index);
    poses[step.name] = { opacity: t, dropMm: (1 - t) * DROP_MM };
  });
  return poses;
}

export function stepLabel(steps: InstallStep[], progress: number): string {
  if (!steps.length) return "No parts";
  if (progress <= 0) return "Start";
  const index = progress >= steps.length
    ? steps.length - 1
    : progress - Math.floor(progress) < 1e-6
      ? Math.floor(progress) - 1
      : Math.floor(progress);
  const step = steps[index];
  return `${index + 1} / ${steps.length} · ${step.name}`;
}
