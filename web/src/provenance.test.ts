import { describe, expect, it } from "vitest";
import { badgeClass, badgeLabel, worstProv } from "./provenance";

describe("worstProv", () => {
  it("returns the single provenance", () => {
    expect(worstProv(["drawing"])).toBe("drawing");
    expect(worstProv(["assumption", "assumption"])).toBe("assumption");
  });
  it("returns mixed for mixed sets (never mislabels as assumption)", () => {
    expect(worstProv(["drawing", "assumption"])).toBe("mixed");
    expect(worstProv(["user_input", "drawing"])).toBe("mixed");
  });
});

describe("badge", () => {
  it("maps known provenances", () => {
    expect(badgeClass("drawing")).toBe("badge b-draw");
    expect(badgeLabel("user_input")).toBe("user");
  });
  it("falls back for mixed/unknown", () => {
    expect(badgeClass("mixed")).toBe("badge b-mixed");
    expect(badgeLabel("mixed")).toBe("mixed");
  });
});
