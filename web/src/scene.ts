/* What the API says about a scene, and how its values read. */

export type Row = { name: string; part: string; group: string; material: string | null; from: number[]; to: number[]; triangles: number; step: number };
export type Feed = { kind: string; text: string };
export type Tick = { id: number; what: string; importance: string; state: string; note: string };
export type Checks = { rounds: { right: number; total: number; passed: boolean }[]; last: { rows: Tick[]; right: number; total: number; passed: boolean; not_in_source: string[] } | null };
export type Scene = { id: string; checks: Checks | null; title: string; state: string; step: string | null; error: string | null; parts: Row[]; feed: Feed[]; unsure: string[]; tokens: number; step_count: number; intents: string[]; model_artifact_id: string | null; preview_artifact_id: string | null; updated_at: number };
export type Card = { id: string; title: string; state: string; parts: number; has_preview: boolean };

export const pretty = (word: string) => word.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
export const mm = (value: number) => (Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(1));
