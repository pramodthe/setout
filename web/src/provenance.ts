/* Provenance badge mapping (extracted for unit testing), plus the part-level
   rules for inferred parts: how they are badged, how faint they render, and
   what the verification outcome tier may say about them. */

import type { Outcome, PartOrigin, Scene } from "./types";

export const PROV_CLASS: Record<string, string> = {
  drawing: "b-draw",
  assumption: "b-assump",
  user_input: "b-user",
  manufacturer_doc: "b-manu",
  inferred: "b-inferred",
};

export const PROV_LABEL: Record<string, string> = {
  drawing: "drawing",
  assumption: "assumption",
  user_input: "user",
  manufacturer_doc: "mfr doc",
  inferred: "inferred",
};

export function worstProv(provenances: string[]): string {
  const ps = new Set(provenances);
  if (ps.size === 1) return [...ps][0];
  return "mixed";
}

export function badgeClass(prov: string): string {
  return `badge ${PROV_CLASS[prov] || "b-mixed"}`;
}

export function badgeLabel(prov: string): string {
  return PROV_LABEL[prov] || "mixed";
}

/* ---------------------------------------------------- part-level provenance */

/** Opacity an inferred part renders at: ghosted, still readable and pickable. */
export const INFERRED_OPACITY = 0.35;

/** `inferred · <confidence to 2 dp, or "unmeasured"> · <inferred_by>`. */
export function inferredBadge(part: PartOrigin): string {
  const confidence = typeof part.confidence === "number" ? part.confidence.toFixed(2) : "unmeasured";
  return `inferred · ${confidence} · ${part.inferred_by || "no source recorded"}`;
}

/** The badge a part carries for what it is (its values have their own): the
    full inferred badge, "confirmed" for an inferred part a reviewer confirmed,
    or none for a part the drawing shows (also every part of an older scene). */
export function partBadge(part: PartOrigin): { cls: string; text: string; short: string } | null {
  if (part.provenance === "inferred") return { cls: "badge b-inferred", text: inferredBadge(part), short: "inferred" };
  if (part.provenance === "user_input") return { cls: "badge b-user", text: "confirmed", short: "confirmed" };
  return null;
}

/** Render opacity for a part: inferred parts are ghosted, the rest unchanged. */
export function partOpacity(part: PartOrigin): number {
  return part.provenance === "inferred" ? INFERRED_OPACITY : 1;
}

/** The parts the camera frames by default: those the drawing shows. An inferred
    part can dwarf the section it completes (a door frame over a sill), so it may
    run out of frame; it stays in the scene. All parts when only inferred exist. */
export function framedParts(parts: (PartOrigin & { id: string })[]): string[] {
  const shown = parts.filter((p) => p.provenance !== "inferred");
  return (shown.length ? shown : parts).map((p) => p.id);
}

/* ---------------------------------------------------- verification outcome */

export const OUTCOME_TEXT: Record<Outcome, string> = {
  verified: "verified",
  complete_with_inferred: "complete, with inferred parts",
  incomplete: "incomplete",
};

export function outcomeText(outcome: Outcome): string {
  return OUTCOME_TEXT[outcome];
}

type Graded = Pick<Scene, "solids" | "verification" | "install_order"> & { quality?: Scene["quality"] };

/** Ids of the parts the verifier lists as inferred, plus any part the scene
    itself tags inferred, in install order (unknown ids last), no duplicates. */
export function inferredParts(scene: Graded): string[] {
  const ids = new Set<string>(scene.quality?.inferred_parts || []);
  for (const s of scene.solids || []) if (s.provenance === "inferred") ids.add(s.id);
  const order = scene.install_order || [];
  const rank = (id: string) => (order.includes(id) ? order.indexOf(id) : order.length);
  return [...ids].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

/** Required slots the verifier found empty (`slot-missing:<slot>` FAILs). */
export function slotsMissing(scene: Pick<Scene, "verification">): string[] {
  return (scene.verification || [])
    .filter((c) => c.status === "FAIL" && c.id.startsWith("slot-missing:"))
    .map((c) => c.id.slice("slot-missing:".length));
}

/** The outcome tier to show. The verifier's own word (quality.outcome) is
    repeated, and the client only ever lowers it: a missing required slot is
    "incomplete" whatever was stated, and a "verified" with inferred parts
    present reads "complete, with inferred parts". The client never promotes
    a version to "verified" on its own: with no stated outcome there is none. */
export function outcomeOf(scene: Graded): Outcome | null {
  const stated = scene.quality?.outcome ?? null;
  if (slotsMissing(scene).length) return "incomplete";
  if (stated === "verified" && inferredParts(scene).length) return "complete_with_inferred";
  return stated;
}
