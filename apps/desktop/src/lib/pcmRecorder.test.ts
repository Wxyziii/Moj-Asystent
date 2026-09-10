import { describe, expect, it } from "vitest";
import { resampleTo16k } from "./pcmRecorder";

describe("PCM recorder sample-rate normalization", () => {
  it("converts browser audio to 16 kHz without changing its duration", () => {
    const source = new Float32Array(48_000).fill(0.25);
    const result = resampleTo16k(source, 48_000);

    expect(result).toHaveLength(16_000);
    expect(result[0]).toBeCloseTo(0.25);
    expect(result.at(-1)).toBeCloseTo(0.25);
  });
});
