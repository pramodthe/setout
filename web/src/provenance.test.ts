import { describe, expect, it } from "vitest";
import {
  INFERRED_OPACITY,
  badgeClass,
  badgeLabel,
  docSummary,
  inferredBadge,
  inferredParts,
  outcomeOf,
  outcomeText,
  partBadge,
  partOpacity,
  slotsMissing,
  worstProv,
} from "./provenance";
import type { CheckResult, Figure, Solid } from "./types";

describe("worstProv", () => {
  it("returns the single provenance", () => {
    expect(worstProv(["drawing"])).toBe("drawing");
    expect(worstProv(["assumption", "assumption"])).toBe("assumption");
  });
  it("returns mixed for mixed sets (never mislabels as assumption)", () => {
    expect(worstProv(["drawing", "assumption"])).toBe("mixed");
    expect(worstProv(["user_input", "drawing"])).toBe("mixed");
  });
  it("treats a derived inferred value like any other provenance", () => {
    expect(worstProv(["inferred"])).toBe("inferred");
    expect(worstProv(["inferred", "drawing"])).toBe("mixed");
  });
});

describe("badge", () => {
  it("maps known provenances", () => {
    expect(badgeClass("drawing")).toBe("badge b-draw");
    expect(badgeLabel("user_input")).toBe("user");
    expect(badgeClass("inferred")).toBe("badge b-inferred");
    expect(badgeLabel("inferred")).toBe("inferred");
  });
  it("falls back for mixed/unknown", () => {
    expect(badgeClass("mixed")).toBe("badge b-mixed");
    expect(badgeLabel("mixed")).toBe("mixed");
  });
});

const BY = "archetype:door_threshold@2026.10.30:jamb";

describe("inferredBadge", () => {
  it("formats the confidence to 2 dp with its source", () => {
    expect(inferredBadge({ confidence: 0.8, inferred_by: BY })).toBe(`inferred · 0.80 · ${BY}`);
    expect(inferredBadge({ confidence: 0.8249, inferred_by: BY })).toBe(`inferred · 0.82 · ${BY}`);
    expect(inferredBadge({ confidence: 1, inferred_by: BY })).toBe(`inferred · 1.00 · ${BY}`);
  });
  it("says unmeasured when the confidence is null or absent", () => {
    expect(inferredBadge({ confidence: null, inferred_by: BY })).toBe(`inferred · unmeasured · ${BY}`);
    expect(inferredBadge({ inferred_by: BY })).toBe(`inferred · unmeasured · ${BY}`);
  });
  it("never invents a source", () => {
    expect(inferredBadge({ confidence: 0.5, inferred_by: null })).toBe("inferred · 0.50 · no source recorded");
  });
});

describe("partBadge", () => {
  it("is null for a drawing part and for parts of older scenes without the field", () => {
    expect(partBadge({ provenance: "drawing" })).toBeNull();
    expect(partBadge({})).toBeNull();
  });
  it("badges an inferred part with the full text and a short row label", () => {
    expect(partBadge({ provenance: "inferred", confidence: 0.82, inferred_by: BY })).toEqual({
      cls: "badge b-inferred",
      text: `inferred · 0.82 · ${BY}`,
      short: "inferred",
    });
  });
  it("badges a confirmed part, keeping its origin out of the drawing class", () => {
    expect(partBadge({ provenance: "user_input", confidence: null, inferred_by: BY })).toEqual({
      cls: "badge b-user",
      text: "confirmed",
      short: "confirmed",
    });
  });
});

describe("partOpacity", () => {
  it("ghosts inferred parts only", () => {
    expect(partOpacity({ provenance: "inferred" })).toBe(INFERRED_OPACITY);
    expect(INFERRED_OPACITY).toBeCloseTo(0.35);
    expect(partOpacity({ provenance: "drawing" })).toBe(1);
    expect(partOpacity({ provenance: "user_input" })).toBe(1);
    expect(partOpacity({})).toBe(1);
  });
});

const solid = (id: string, extra: Partial<Solid> = {}): Solid =>
  ({ id, name: id, source_label: null, representation: "solid", layer: id, color: "#999", note: null,
     tris: [], tri_count: 0, bounds: null, params: [], product: null, install_after: [], source_regions: [], ...extra }) as Solid;

const check = (id: string, status: CheckResult["status"], required?: boolean): CheckResult =>
  ({ id, stage: "B", description: id, status, expected: null, actual: null, tolerance: null, evidence: null,
     ...(required === undefined ? {} : { required }) });

describe("inferredParts", () => {
  it("joins the verifier's list with parts the scene tags inferred, in install order, once each", () => {
    const scene = {
      install_order: ["slab", "jamb", "leaf"],
      solids: [solid("slab"), solid("jamb", { provenance: "inferred" }), solid("leaf", { provenance: "inferred" })],
      verification: [],
      quality: { outcome: "complete_with_inferred" as const, inferred_parts: ["leaf", "head"] },
    };
    expect(inferredParts(scene)).toEqual(["jamb", "leaf", "head"]);
  });
  it("is empty for an older scene", () => {
    expect(inferredParts({ install_order: ["slab"], solids: [solid("slab")], verification: [] })).toEqual([]);
  });
  it("ignores confirmed parts", () => {
    const scene = { install_order: ["jamb"], solids: [solid("jamb", { provenance: "user_input" })], verification: [] };
    expect(inferredParts(scene)).toEqual([]);
  });
});

describe("slotsMissing", () => {
  it("names the slots whose check failed", () => {
    const verification = [check("slot-missing:jamb", "FAIL"), check("slot-missing:leaf", "PASS"), check("contact:a:b", "FAIL")];
    expect(slotsMissing({ verification })).toEqual(["jamb"]);
  });
});

describe("outcomeOf", () => {
  const plain = { install_order: ["slab"], solids: [solid("slab")], verification: [check("contact:a:b", "PASS", true)] };
  it("repeats the verifier's outcome", () => {
    expect(outcomeOf({ ...plain, quality: { outcome: "verified" } })).toBe("verified");
    expect(outcomeOf({ ...plain, quality: { outcome: "incomplete" } })).toBe("incomplete");
    expect(outcomeOf({ ...plain, quality: { outcome: "complete_with_inferred" } })).toBe("complete_with_inferred");
  });
  it("never presents inferred parts as verified", () => {
    const withInferred = { ...plain, solids: [solid("slab"), solid("jamb", { provenance: "inferred" })] };
    expect(outcomeOf({ ...withInferred, quality: { outcome: "verified" } })).toBe("complete_with_inferred");
    expect(outcomeOf({ ...plain, quality: { outcome: "verified", inferred_parts: ["jamb"] } })).toBe("complete_with_inferred");
  });
  it("is incomplete whenever a required slot is missing, whatever was stated", () => {
    const missing = { ...plain, verification: [check("slot-missing:jamb", "FAIL", true)] };
    expect(outcomeOf({ ...missing, quality: { outcome: "verified" } })).toBe("incomplete");
    expect(outcomeOf(missing)).toBe("incomplete");
  });
  it("claims nothing when the verifier said nothing", () => {
    expect(outcomeOf(plain)).toBeNull();
    expect(outcomeOf({ ...plain, quality: {} })).toBeNull();
    expect(outcomeOf({ ...plain, quality: { outcome: null } })).toBeNull();
    // all checks pass, but no outcome was decided: the client never promotes to verified
    expect(outcomeOf({ ...plain, solids: [solid("slab"), solid("jamb", { provenance: "inferred" })] })).toBeNull();
  });
});

describe("outcomeText", () => {
  it("puts each tier in words", () => {
    expect(outcomeText("verified")).toBe("verified");
    expect(outcomeText("complete_with_inferred")).toBe("complete, with inferred parts");
    expect(outcomeText("incomplete")).toBe("incomplete");
  });
});

describe("docSummary", () => {
  const figure = (kind: Figure["kind"], dpi: number | null): Figure =>
    ({ crop_id: "p0_x", page: 0, width: 400, height: 300, kind, dpi });

  it("never calls a rendered page an extracted image", () => {
    const figures = [figure("embedded_image", null), figure("embedded_image", null), figure("rendered_page", 300)];
    expect(docSummary({ pages: 3, figures, warnings: [] })).toBe(
      "3 page(s), 2 extracted image(s), lossless, 1 rendered page(s).");
    expect(docSummary({ pages: 1, figures: [figures[2]], warnings: [] })).toBe("1 page(s), 1 rendered page(s).");
  });

  it("says when nothing was found, and passes on the renderer's warnings", () => {
    expect(docSummary({ pages: 2, figures: [], warnings: ["page 2 could not be rendered: x"] })).toBe(
      "2 page(s), no figures. page 2 could not be rendered: x");
  });
});
