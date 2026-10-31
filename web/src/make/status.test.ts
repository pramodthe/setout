import { describe, expect, it } from "vitest";
import { attemptLabel } from "./status";

describe("attempt label", () => {
  it("names a repair with the attempt cap", () => {
    expect(attemptLabel(2, "running")).toBe("Repairing, attempt 2 of 4");
    expect(attemptLabel(1, "running")).toBe("Building, attempt 1 of 4");
    expect(attemptLabel(null, "reconnecting")).toBe("Reconnecting…");
    expect(attemptLabel(4, "failed")).toBe("The build failed.");
  });
});