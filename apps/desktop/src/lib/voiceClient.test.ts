import { afterEach, expect, it, vi } from "vitest";
import { getVoiceSettings, updateVoiceVocabulary } from "./voiceClient";

const credential = "V".repeat(43);
const payload = {
  active: {
    profile_id: "preferred",
    model: "large-v3-turbo",
    device: "cuda",
    compute_type: "int8_float16",
    beam_size: 5,
    best_of: 5,
    patience: 1,
    temperature: 0,
    condition_on_previous_text: false,
    no_speech_threshold: 0.6,
    log_probability_threshold: -1,
    compression_ratio_threshold: 2.4,
    status: "ready",
    detail: null,
    fallback_active: false,
    fallback_reason: null,
  },
  profiles: [],
  vocabulary: ["GitHub", "Qwen"],
  vad: {
    pre_roll_ms: 240,
    post_roll_ms: 240,
    trailing_silence_ms: 560,
    maximum_utterance_seconds: 30,
  },
};

afterEach(() => vi.unstubAllGlobals());

it("reads the active local STT profile", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => payload }),
  );

  await expect(getVoiceSettings(credential)).resolves.toEqual(payload);
});

it("writes vocabulary through the authenticated versioned request", async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ ...payload, vocabulary: ["GitHub", "siekiera"] }),
  });
  vi.stubGlobal("fetch", fetch);

  await updateVoiceVocabulary(credential, ["GitHub", "siekiera"]);

  expect(fetch).toHaveBeenCalledWith(
    "http://127.0.0.1:8765/voice/settings",
    expect.objectContaining({
      method: "PATCH",
      redirect: "error",
      body: JSON.stringify({
        protocol_version: "1.4",
        vocabulary: ["GitHub", "siekiera"],
      }),
    }),
  );
});

it("rejects malformed voice status and non-local core URLs", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ...payload, active: { model: "unknown" } }),
    }),
  );

  await expect(getVoiceSettings(credential)).rejects.toThrow("głosu");
  await expect(
    getVoiceSettings(credential, "https://example.com:8765"),
  ).rejects.toThrow("lokalny");
});
