import { describe, expect, it } from "vitest";
import { logicalSelectionToPhysical } from "./regionSelection";

describe("logicalSelectionToPhysical", () => {
  it("preserves negative monitor origins at 150% DPI", () => {
    expect(
      logicalSelectionToPhysical(
        { x: 100, y: 80 },
        { x: 300, y: 200 },
        {
          left: -2560,
          top: -200,
          width: 2560,
          height: 1440,
          scale_factor: 1.5,
        },
      ),
    ).toEqual({ left: -2410, top: -80, right: -2110, bottom: 100 });
  });

  it("orders reversed drags and clamps them to one monitor", () => {
    expect(
      logicalSelectionToPhysical(
        { x: 900, y: 700 },
        { x: -20, y: 10 },
        { left: 0, top: 0, width: 1600, height: 1200, scale_factor: 2 },
      ),
    ).toEqual({ left: 0, top: 20, right: 1600, bottom: 1200 });
  });
});
