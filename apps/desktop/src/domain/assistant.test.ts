import { describe, expect, it } from "vitest";
import { canTransition } from "./assistant";

describe("assistant state transitions", () => {
  it("allows the simulated voice path", () => {
    expect(canTransition("idle", "wake_detected")).toBe(true);
    expect(canTransition("wake_detected", "listening")).toBe(true);
    expect(canTransition("listening", "transcribing")).toBe(true);
    expect(canTransition("transcribing", "thinking")).toBe(true);
    expect(canTransition("thinking", "speaking")).toBe(true);
    expect(canTransition("speaking", "follow_up")).toBe(true);
  });

  it("rejects impossible simulated transitions", () => {
    expect(canTransition("idle", "speaking")).toBe(false);
    expect(canTransition("error", "thinking")).toBe(false);
  });
});
