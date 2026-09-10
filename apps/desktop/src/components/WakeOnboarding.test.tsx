import { describe, expect, it } from "vitest";
import type { RecordingStep } from "@moj-asystent/protocol";
import { findNextMissingRecordingIndex } from "./recordingNavigation";

const curriculum = ["wake-3", "wake-4", "wake-12"].map(
  (id) => ({ id }) as RecordingStep,
);

describe("wake onboarding recovery navigation", () => {
  it("skips samples already accepted after recording a missing step", () => {
    expect(findNextMissingRecordingIndex(curriculum, ["wake-3"], 1)).toBe(1);
    expect(
      findNextMissingRecordingIndex(curriculum, ["wake-3", "wake-4"], 1),
    ).toBe(2);
  });
});
