import { describe, expect, it } from "vitest";
import { DROP_MM, easeOut, installSteps, posesAt, stepLabel } from "./playback";

const nodes = [
  { name: "Tile", build_order: 4 },
  { name: "Substrate", build_order: 0 },
  { name: "Upturn", build_order: 3 },
  { name: "Screed", build_order: 1 },
];

describe("install sequence", () => {
  it("orders parts by build_order, not tree order", () => {
    expect(installSteps(nodes).map((step) => step.name)).toEqual(["Substrate", "Screed", "Upturn", "Tile"]);
  });

  it("drops a part 50 mm and fades it in during its step", () => {
    const steps = installSteps(nodes);
    const start = posesAt(steps, 0);
    expect(start.Substrate).toEqual({ opacity: 0, dropMm: DROP_MM });
    const mid = posesAt(steps, 2.5);
    expect(mid.Substrate.opacity).toBe(1);
    expect(mid.Screed.opacity).toBe(1);
    expect(mid.Upturn.opacity).toBeCloseTo(easeOut(0.5));
    expect(mid.Upturn.dropMm).toBeCloseTo((1 - easeOut(0.5)) * DROP_MM);
    expect(mid.Tile.opacity).toBe(0);
    expect(mid.Tile.dropMm).toBe(DROP_MM);
  });

  it("names the part that is arriving", () => {
    const steps = installSteps(nodes);
    expect(stepLabel(steps, 0)).toBe("Start");
    expect(stepLabel(steps, 2.5)).toBe("3 / 4 · Upturn");
    expect(stepLabel(steps, 4)).toBe("4 / 4 · Tile");
  });
});
