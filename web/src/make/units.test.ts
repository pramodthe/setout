import { describe, expect, it } from "vitest";
import { needsZUpMmConversion, zUpMmToYUpMetres } from "./units";

describe("zUpMmToYUpMetres", () => {
  it("turns Blender Z-up millimetres into Y-up metres", () => {
    const [x, y, z] = zUpMmToYUpMetres(100, 200, 50);
    expect(x).toBeCloseTo(0.1);
    expect(y).toBeCloseTo(0.05);
    expect(z).toBeCloseTo(-0.2);
  });
});

describe("needsZUpMmConversion", () => {
  it("leaves a Y-up metre glb alone and converts a millimetre scene", () => {
    expect(needsZUpMmConversion(0.4)).toBe(false);
    expect(needsZUpMmConversion(400)).toBe(true);
  });
});
