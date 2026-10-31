import { describe, expect, it } from "vitest";
import {
  axisScaleFactors,
  diffOverrides,
  redoHistory,
  undoHistory,
  yUpMetresToZUpMm,
  type History,
  type OverrideMap,
  type TreeNode,
} from "./edits";
import { zUpMmToYUpMetres } from "./units";

const upturn: TreeNode = {
  name: "Upturn",
  type: "mesh",
  location: [91, 200, 200],
  rotation_deg: [0, 0, 0],
  dims: { upturn_hgt: 100, upturn_thk: 2 },
  material: "membrane",
};

describe("yUpMetresToZUpMm", () => {
  it("inverts the glTF Y-up conversion", () => {
    const yup = zUpMmToYUpMetres(91, 200, 200);
    expect(yUpMetresToZUpMm(yup[0], yup[1], yup[2]).map((n) => Math.round(n))).toEqual([91, 200, 200]);
  });
});

describe("override history", () => {
  it("undoes and redoes a dimension edit", () => {
    const start: History = { past: [], present: {}, future: [] };
    const edited: OverrideMap = { Upturn: { dims: { upturn_hgt: 160 } } };
    const committed: History = { past: [start.present], present: edited, future: [] };
    const undone = undoHistory(committed);
    expect(undone.present).toEqual({});
    const redone = redoHistory(undone);
    expect(redone.present).toEqual(edited);
    expect(diffOverrides({ Upturn: upturn }, redone.present).overrides).toEqual([
      { name: "Upturn", dims: { upturn_hgt: 160 } },
    ]);
  });

  it("scales the axis whose span matches the old dimension", () => {
    expect(axisScaleFactors([2, 400, 100], { upturn_hgt: 100, floor_width: 400 }, { upturn_hgt: 160 })).toEqual([
      1, 1, 1.6,
    ]);
  });
});
