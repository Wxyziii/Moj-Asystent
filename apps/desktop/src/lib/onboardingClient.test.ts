import { afterEach, describe, expect, it, vi } from "vitest";
import { beginOnboarding } from "./onboardingClient";

afterEach(() => vi.unstubAllGlobals());

describe("authenticated wake onboarding client", () => {
  it("sends the exact protocol version and bearer credential", async () => {
    const response = {
      session_id: "4fa6ec1d-3379-4c4c-9451-93e515d91e12",
      name: {
        display_name: "Żorina",
        normalized_name: "żorina",
        score: 90,
        rating: "bardzo dobra",
        false_trigger_risk: "niskie",
        syllable_count: 3,
        trainable: true,
        warnings: [],
        explanation: "Wyraźne brzmienie.",
      },
      microphone_device: "Mikrofon",
      keep_training_samples: false,
      curriculum: [],
      accepted_step_ids: [],
      calibration: null,
      training_job_id: null,
      candidate_ready: false,
      validation: null,
    };
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    });
    vi.stubGlobal("fetch", fetch);

    await beginOnboarding("secret", "Żorina", "Mikrofon", false);

    const init = fetch.mock.calls[0][1] as RequestInit;
    expect(init.headers).toMatchObject({ Authorization: "Bearer secret" });
    expect(JSON.parse(String(init.body))).toMatchObject({
      protocol_version: "1.2",
      name: "Żorina",
    });
  });
});
