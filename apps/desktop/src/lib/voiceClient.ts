import { PROTOCOL_VERSION } from "@moj-asystent/protocol";

export type VoiceRuntimeState =
  | "ready"
  | "loading"
  | "unavailable"
  | "missing_model"
  | "cuda_unavailable"
  | "fallback_active"
  | "transcription_failure";

export interface VoiceProfile {
  profile_id: string;
  model: string;
  device: "cpu" | "cuda";
  compute_type: "int8" | "int8_float16" | "float16" | "float32";
  beam_size: number;
  best_of: number;
  patience: number;
  temperature: number;
  condition_on_previous_text: boolean;
  no_speech_threshold: number;
  log_probability_threshold: number;
  compression_ratio_threshold: number;
}

export interface VoiceSettings {
  active: VoiceProfile & {
    status: VoiceRuntimeState;
    detail: string | null;
    fallback_active: boolean;
    fallback_reason: string | null;
  };
  profiles: VoiceProfile[];
  vocabulary: string[];
  vad: {
    pre_roll_ms: number;
    post_roll_ms: number;
    trailing_silence_ms: number;
    maximum_utterance_seconds: number;
  };
}

const defaultUrl = import.meta.env.VITE_CORE_URL ?? "http://127.0.0.1:8765";
const credentialPattern = /^[A-Za-z0-9_-]{43,128}$/;

export async function getVoiceSettings(
  credential: string,
  baseUrl = defaultUrl,
): Promise<VoiceSettings> {
  return parseVoiceSettings(
    await requestJson("/voice/settings", credential, baseUrl),
  );
}

export async function updateVoiceVocabulary(
  credential: string,
  vocabulary: string[],
  baseUrl = defaultUrl,
): Promise<VoiceSettings> {
  return parseVoiceSettings(
    await requestJson("/voice/settings", credential, baseUrl, {
      method: "PATCH",
      body: JSON.stringify({ protocol_version: PROTOCOL_VERSION, vocabulary }),
    }),
  );
}

async function requestJson(
  path: string,
  credential: string,
  baseUrl: string,
  init: RequestInit = {},
): Promise<unknown> {
  if (!credentialPattern.test(credential))
    throw new Error("Nieprawidłowa sesja rdzenia.");
  const origin = validateUrl(baseUrl).origin;
  const response = await fetch(origin + path, {
    ...init,
    cache: "no-store",
    redirect: "error",
    headers: {
      Authorization: `Bearer ${credential}`,
      "Content-Type": "application/json",
      ...init.headers,
    },
  });
  const value: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      isRecord(value) && typeof value.detail === "string"
        ? value.detail
        : "Rdzeń odrzucił ustawienia głosu.";
    throw new Error(detail);
  }
  return value;
}

function parseVoiceSettings(value: unknown): VoiceSettings {
  if (
    !isRecord(value) ||
    !isActiveProfile(value.active) ||
    !Array.isArray(value.profiles) ||
    !value.profiles.every(isProfile) ||
    !Array.isArray(value.vocabulary) ||
    !value.vocabulary.every(
      (entry) => typeof entry === "string" && entry.length > 0,
    ) ||
    !isVad(value.vad)
  )
    throw new Error("Rdzeń zwrócił nieprawidłowe ustawienia głosu.");
  return value as unknown as VoiceSettings;
}

function isActiveProfile(value: unknown): value is VoiceSettings["active"] {
  if (!isProfile(value)) return false;
  const active = value as VoiceProfile & Record<string, unknown>;
  return (
    isRuntimeState(active.status) &&
    (active.detail === null || typeof active.detail === "string") &&
    typeof active.fallback_active === "boolean" &&
    (active.fallback_reason === null ||
      typeof active.fallback_reason === "string")
  );
}

function isProfile(value: unknown): value is VoiceProfile {
  if (!isRecord(value)) return false;
  return (
    typeof value.profile_id === "string" &&
    typeof value.model === "string" &&
    ["cpu", "cuda"].includes(String(value.device)) &&
    ["int8", "int8_float16", "float16", "float32"].includes(
      String(value.compute_type),
    ) &&
    Number.isInteger(value.beam_size) &&
    Number.isInteger(value.best_of) &&
    typeof value.patience === "number" &&
    typeof value.temperature === "number" &&
    typeof value.condition_on_previous_text === "boolean" &&
    typeof value.no_speech_threshold === "number" &&
    typeof value.log_probability_threshold === "number" &&
    typeof value.compression_ratio_threshold === "number"
  );
}

function isRuntimeState(value: unknown): value is VoiceRuntimeState {
  return [
    "ready",
    "loading",
    "unavailable",
    "missing_model",
    "cuda_unavailable",
    "fallback_active",
    "transcription_failure",
  ].includes(String(value));
}

function isVad(value: unknown): boolean {
  return (
    isRecord(value) &&
    Number.isInteger(value.pre_roll_ms) &&
    Number.isInteger(value.post_roll_ms) &&
    Number.isInteger(value.trailing_silence_ms) &&
    typeof value.maximum_utterance_seconds === "number"
  );
}

function validateUrl(value: string): URL {
  const parsed = new URL(value);
  if (
    parsed.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname) ||
    parsed.username ||
    parsed.password ||
    (parsed.pathname !== "/" && parsed.pathname !== "") ||
    parsed.search ||
    parsed.hash ||
    !parsed.port
  )
    throw new Error("Adres rdzenia musi być lokalny.");
  return parsed;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
