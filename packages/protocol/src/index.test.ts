import { describe, expect, it } from "vitest";
import { PROTOCOL_VERSION, parseProtocolEvent } from "./index";

describe("protocol-v1 parser", () => {
  it("accepts a core health event and rejects an unknown protocol", () => {
    expect(
      parseProtocolEvent({
        protocol_version: PROTOCOL_VERSION,
        event_id: "id",
        occurred_at: "2026-09-09T20:00:00Z",
        correlation_id: null,
        type: "system.health",
        payload: {
          service: "core",
          status: "ready",
          protocol_version: PROTOCOL_VERSION,
          assistant_state: "idle",
        },
      })?.type,
    ).toBe("system.health");
    expect(
      parseProtocolEvent({
        protocol_version: "2.0",
        type: "system.health",
        payload: {},
      }),
    ).toBeNull();
  });
});
