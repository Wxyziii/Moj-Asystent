import {
  PROTOCOL_VERSION,
  parseOnboardingSession,
  parseSampleQuality,
  parseTrainingJob,
  type OnboardingSession,
  type SampleQuality,
  type TrainingJob,
  type ValidationMetrics,
  type WakeModelMetadata,
} from "@moj-asystent/protocol";

const coreOrigin = import.meta.env.VITE_CORE_URL ?? "http://127.0.0.1:8765";

export interface CapturedPcm {
  pcmBase64: string;
  sampleRate: number;
}

export async function getOnboardingStatus(
  credential: string,
): Promise<{ completed: boolean; active: WakeModelMetadata | null }> {
  const value = await request("/onboarding/status", credential);
  if (
    typeof value !== "object" ||
    value === null ||
    !("completed" in value) ||
    typeof value.completed !== "boolean"
  )
    throw new Error("Nieprawidłowy status konfiguracji.");
  return value as { completed: boolean; active: WakeModelMetadata | null };
}

async function request(
  path: string,
  credential: string,
  init: RequestInit = {},
): Promise<unknown> {
  const response = await fetch(coreOrigin + path, {
    ...init,
    cache: "no-store",
    redirect: "error",
    headers: {
      Authorization: `Bearer ${credential}`,
      "Content-Type": "application/json",
      ...init.headers,
    },
  });
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String(body.detail)
        : "Core odrzucił żądanie.";
    throw new Error(detail);
  }
  return body;
}

export async function beginOnboarding(
  credential: string,
  name: string,
  microphoneDevice: string | null,
  keepTrainingSamples: boolean,
): Promise<OnboardingSession> {
  const value = await request("/onboarding/sessions", credential, {
    method: "POST",
    body: JSON.stringify({
      protocol_version: PROTOCOL_VERSION,
      name,
      microphone_device: microphoneDevice,
      keep_training_samples: keepTrainingSamples,
    }),
  });
  const parsed = parseOnboardingSession(value);
  if (!parsed)
    throw new Error("Core zwrócił nieprawidłową sesję konfiguracji.");
  return parsed;
}

export async function getOnboardingSession(
  credential: string,
  sessionId: string,
): Promise<OnboardingSession> {
  const value = await request(`/onboarding/sessions/${sessionId}`, credential);
  const parsed = parseOnboardingSession(value);
  if (!parsed) throw new Error("Core zwrócił nieprawidłową sesję konfiguracji.");
  return parsed;
}

export async function sendCalibration(
  credential: string,
  sessionId: string,
  audio: CapturedPcm,
): Promise<{ ready: boolean; message: string; noise_floor_rms: number }> {
  const value = await pcmRequest(
    "/onboarding/calibration",
    credential,
    sessionId,
    audio,
  );
  if (
    typeof value !== "object" ||
    value === null ||
    !("ready" in value) ||
    !("message" in value) ||
    !("noise_floor_rms" in value)
  )
    throw new Error("Nieprawidłowy wynik kalibracji.");
  return value as { ready: boolean; message: string; noise_floor_rms: number };
}

export async function sendSample(
  credential: string,
  sessionId: string,
  stepId: string,
  audio: CapturedPcm,
): Promise<SampleQuality> {
  const value = await request("/onboarding/samples", credential, {
    method: "POST",
    body: JSON.stringify({
      protocol_version: PROTOCOL_VERSION,
      session_id: sessionId,
      step_id: stepId,
      sample_rate: audio.sampleRate,
      pcm_s16le: audio.pcmBase64,
    }),
  });
  const parsed = parseSampleQuality(value);
  if (!parsed) throw new Error("Nieprawidłowa ocena próbki.");
  return parsed;
}

export async function startTraining(
  credential: string,
  sessionId: string,
): Promise<TrainingJob> {
  const value = await request("/onboarding/training", credential, {
    method: "POST",
    body: JSON.stringify({
      protocol_version: PROTOCOL_VERSION,
      session_id: sessionId,
      seed: 44,
    }),
  });
  const parsed = parseTrainingJob(value);
  if (!parsed) throw new Error("Nieprawidłowy status treningu.");
  return parsed;
}

export async function getTraining(
  credential: string,
  jobId: string,
): Promise<TrainingJob> {
  const parsed = parseTrainingJob(
    await request(`/onboarding/training/${jobId}`, credential),
  );
  if (!parsed) throw new Error("Nieprawidłowy status treningu.");
  return parsed;
}

export async function cancelTraining(
  credential: string,
  jobId: string,
): Promise<void> {
  await request(`/onboarding/training/${jobId}/cancel`, credential, {
    method: "POST",
  });
}

export async function cancelOnboarding(
  credential: string,
  sessionId: string,
): Promise<void> {
  await request(`/onboarding/sessions/${sessionId}`, credential, {
    method: "DELETE",
  });
}

export async function sendValidation(
  credential: string,
  sessionId: string,
  kind: "positive" | "negative",
  audio: CapturedPcm,
): Promise<ValidationMetrics> {
  return (await request("/onboarding/validation", credential, {
    method: "POST",
    body: JSON.stringify({
      protocol_version: PROTOCOL_VERSION,
      session_id: sessionId,
      kind,
      sample_rate: audio.sampleRate,
      pcm_s16le: audio.pcmBase64,
    }),
  })) as ValidationMetrics;
}

export async function activateWakeModel(
  credential: string,
  sessionId: string,
  allowOverride = false,
): Promise<WakeModelMetadata> {
  return (await request("/onboarding/activate", credential, {
    method: "POST",
    body: JSON.stringify({
      protocol_version: PROTOCOL_VERSION,
      session_id: sessionId,
      allow_override: allowOverride,
    }),
  })) as WakeModelMetadata;
}

export async function setWakeSensitivity(
  credential: string,
  value: number,
): Promise<void> {
  await request("/onboarding/sensitivity", credential, {
    method: "PATCH",
    body: JSON.stringify({ protocol_version: PROTOCOL_VERSION, value }),
  });
}

async function pcmRequest(
  path: string,
  credential: string,
  sessionId: string,
  audio: CapturedPcm,
): Promise<unknown> {
  return request(path, credential, {
    method: "POST",
    body: JSON.stringify({
      protocol_version: PROTOCOL_VERSION,
      session_id: sessionId,
      sample_rate: audio.sampleRate,
      pcm_s16le: audio.pcmBase64,
    }),
  });
}
