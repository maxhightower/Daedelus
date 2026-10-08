import { describe, expect, it } from "vitest";
import { colName, parseInput, parseRef } from "../canvas/office";

describe("office grid helpers", () => {
  it("names columns like a spreadsheet", () => {
    expect([0, 1, 25, 26, 27, 701, 702].map(colName)).toEqual(["A", "B", "Z", "AA", "AB", "ZZ", "AAA"]);
  });
  it("parses cell and range references (sheet prefix, absolute refs)", () => {
    expect(parseRef("B9")).toEqual({ c1: 1, r1: 8, c2: 1, r2: 8 });
    expect(parseRef("Summary!$A$1:$C$9")).toEqual({ c1: 0, r1: 0, c2: 2, r2: 8 });
    expect(parseRef("not a ref")).toBeNull();
  });
  it("keeps formulas as text and numbers as numbers", () => {
    expect(parseInput("=B2-B1")).toBe("=B2-B1");
    expect(parseInput("420.5")).toBe(420.5);
    expect(parseInput("1950s")).toBe("1950s");
    expect(parseInput("")).toBe("");
  });
});
