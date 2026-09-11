import { describe, expect, it } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import cases from "../fixtures/contract-cases.json";
import schema from "../schema/protocol-v1.json";
import {
  createClientHello,
  parseOnboardingSession,
  parseProtocolEvent,
} from "./index";

describe("shared protocol acceptance corpus", () => {
  const ajv = new Ajv2020({ strict: true });
  addFormats(ajv);
  const validateSchema = ajv.compile(schema);

  it.each(cases)("$name", ({ value, valid }) => {
    expect(validateSchema(value)).toBe(valid);
    expect(parseProtocolEvent(value) !== null).toBe(valid);
  });
  it("serializes the desktop hello to the shared contract", () => {
    const hello = createClientHello("desktop");
    expect(parseProtocolEvent(JSON.parse(JSON.stringify(hello)))).toEqual(
      hello,
    );
  });

  it("validates onboarding sessions received from the local core", () => {
    const value = {
      session_id: "4fa6ec1d-3379-4c4c-9451-93e515d91e12",
      name: {
        display_name: "Żorina",
        normalized_name: "żorina",
        score: 93,
        rating: "bardzo dobra",
        false_trigger_risk: "niskie",
        syllable_count: 3,
        trainable: true,
        warnings: [],
        explanation: "Wyraźna nazwa.",
      },
      microphone_device: null,
      keep_training_samples: false,
      curriculum: [
        {
          id: "wake-1",
          kind: "positive",
          phrase: "Żorina",
          loudness: "normalnie",
          distance: "50–80 cm",
          intonation: "neutralnie",
          posture: "prosto",
          guidance: "Przykład aktywujący.",
          avoid: "Nie przeciągaj sylab.",
          expected_seconds: [0.5, 3],
        },
      ],
      accepted_step_ids: [],
      calibration: null,
      training_job_id: null,
      candidate_ready: false,
      validation: null,
    };
    expect(parseOnboardingSession(value)).toEqual(value);
    expect(
      parseOnboardingSession({ ...value, session_id: "../escape" }),
    ).toBeNull();
  });
});
