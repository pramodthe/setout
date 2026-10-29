import { describe, expect, it } from "vitest";
import { SPECS, materialClassFor, tileNoise } from "./materials";
import { boxProjectUVs } from "./uv";

describe("materialClassFor", () => {
  it("maps the seed component types to sensible materials", () => {
    expect(materialClassFor("substrate")).toBe("concrete");
    expect(materialClassFor("membrane")).toBe("membrane");
    expect(materialClassFor("screed")).toBe("screed");
    expect(materialClassFor("sealant_bead")).toBe("sealant");
    expect(materialClassFor("door_frame")).toBe("aluminium");
    expect(materialClassFor("waterstop")).toBe("aluminium");
    expect(materialClassFor("protection_sheet")).toBe("sheet");
    expect(materialClassFor("fixing_rod")).toBe("steel");
    expect(materialClassFor("packer")).toBe("plastic");
  });
  it("resolves ordering traps", () => {
    expect(materialClassFor("backing_rod")).toBe("foam"); // not steel rod
    expect(materialClassFor("cfc_packers")).toBe("fibre_cement"); // not plastic
    expect(materialClassFor("chemical_grout")).toBe("grout");
  });
  it("maps fixtures and glazing", () => {
    expect(materialClassFor("bath")).toBe("ceramic");
    expect(materialClassFor("wc")).toBe("ceramic");
    expect(materialClassFor("shower_tray")).toBe("ceramic");
    expect(materialClassFor("shower_screen")).toBe("glass");
    expect(materialClassFor("door")).toBe("timber");
    expect(materialClassFor("door_frame")).toBe("aluminium");
    expect(materialClassFor("vanity")).toBe("timber");
    expect(materialClassFor("floor_grate")).toBe("steel");
    expect(materialClassFor("tap_body")).toBe("steel");
    expect(materialClassFor("tape")).toBe("tape"); // not a tap
  });
  it("falls back to generic", () => {
    expect(materialClassFor("termination_datum")).toBe("generic");
  });
  it("every class has a positive real-world repeat", () => {
    for (const s of Object.values(SPECS)) expect(s.repeatMm).toBeGreaterThan(0);
  });
});

describe("tileNoise", () => {
  it("is deterministic, bounded and seamless at the wrap", () => {
    const mk = () => {
      let a = 1;
      return () => ((a = (a * 16807) % 2147483647) / 2147483647);
    };
    const n = 64;
    const A = tileNoise(n, [4, 8], mk());
    const B = tileNoise(n, [4, 8], mk());
    expect(Array.from(A)).toEqual(Array.from(B));
    for (const v of A) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    }
    // last column flows into the first: neighbours differ only slightly
    for (let y = 0; y < n; y++) {
      expect(Math.abs(A[y * n + n - 1] - A[y * n])).toBeLessThan(0.15);
    }
  });
});

describe("boxProjectUVs", () => {
  it("projects a floor triangle onto x/z at real scale", () => {
    const tri = [0, 0, 0, 600, 0, 0, 0, 0, 300]; // flat on y=0
    const uv = Array.from(boxProjectUVs(tri, 600));
    expect(uv).toEqual([0, 0, 1, 0, 0, 0.5]);
  });
  it("projects a section-face triangle onto x/y", () => {
    const tri = [0, 0, 5, 100, 0, 5, 0, 50, 5]; // in plane z=5
    const uv = Array.from(boxProjectUVs(tri, 100));
    expect(uv).toEqual([0, 0, 1, 0, 0, 0.5]);
  });
  it("projects an x-facing triangle onto z/y", () => {
    const tri = [10, 0, 0, 10, 0, 200, 10, 100, 0];
    const uv = Array.from(boxProjectUVs(tri, 200));
    expect(uv).toEqual([0, 0, 1, 0, 0, 0.5]);
  });
});
