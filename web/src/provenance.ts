/* Provenance badge mapping (extracted for unit testing). */

export const PROV_CLASS: Record<string, string> = {
  drawing: "b-draw",
  assumption: "b-assump",
  user_input: "b-user",
  manufacturer_doc: "b-manu",
};

export const PROV_LABEL: Record<string, string> = {
  drawing: "drawing",
  assumption: "assumption",
  user_input: "user",
  manufacturer_doc: "mfr doc",
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
