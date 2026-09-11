export const PROTOCOL_VERSION = "1.3" as const;

export const assistantStates = [
  "idle",
  "wake_detected",
  "listening",
  "transcribing",
  "thinking",
  "speaking",
  "follow_up",
  "error",
] as const;
export type AssistantState = (typeof assistantStates)[number];

export interface EventBase {
  protocol_version: typeof PROTOCOL_VERSION;
  event_id: string;
  occurred_at: string;
  correlation_id: string | null;
}
export interface ClientHello extends EventBase {
  type: "client.hello";
  payload: { client_id: string; protocol_version: typeof PROTOCOL_VERSION };
}
export interface SystemHealth extends EventBase {
  type: "system.health";
  payload: {
    service: "core";
    status: "ready" | "stopping";
    protocol_version: typeof PROTOCOL_VERSION;
    assistant_state: AssistantState;
  };
}
export interface AssistantStateChanged extends EventBase {
  type: "assistant.state.changed";
  payload: { previous_state: AssistantState | null; state: AssistantState };
}
export interface AudioTranscriptFinal extends EventBase {
  type: "audio.transcript.final";
  payload: {
    operation_id: string;
    text: string;
    language: "pl";
    duration_ms: number;
  };
}
export interface AssistantResponseCompleted extends EventBase {
  type: "assistant.response.completed";
  payload: {
    operation_id: string;
    text: string;
    spoken_text: string | null;
    kind: "local_model";
    model: string;
  };
}
export interface AssistantResponseStarted extends EventBase {
  type: "assistant.response.started";
  payload: { operation_id: string; model: string; mode: "voice" | "text" };
}
export interface AssistantResponseDelta extends EventBase {
  type: "assistant.response.delta";
  payload: { operation_id: string; sequence: number; text: string };
}
export interface ModelStatusChanged extends EventBase {
  type: "model.status.changed";
  payload: {
    provider: "ollama";
    model: string;
    status: "unavailable" | "missing" | "loading" | "ready" | "error";
    detail: string | null;
  };
}
export interface ToolExecutionStatus extends EventBase {
  type: "tool.execution.status";
  payload: {
    operation_id: string;
    call_id: string;
    tool_name: string;
    status: "requested" | "confirmation_required" | "executing";
  };
}
export interface ConfirmationDetail {
  label: string;
  value: string;
}
export interface ToolConfirmationRequested extends EventBase {
  type: "tool.confirmation.requested";
  payload: {
    confirmation_id: string;
    operation_id: string;
    call_id: string;
    tool_name: string;
    arguments_digest: string;
    action: string;
    target: string;
    details: ConfirmationDetail[];
    risk: string;
    expires_at: string;
    persistent_allowed: boolean;
  };
}
export interface ToolConfirmationResolved extends EventBase {
  type: "tool.confirmation.resolved";
  payload: {
    confirmation_id: string;
    operation_id: string;
    call_id: string;
    tool_name: string;
    decision: "allow" | "cancel" | "always_allow";
  };
}
export interface ToolResult extends EventBase {
  type: "tool.result";
  payload: {
    operation_id: string;
    call_id: string;
    tool_name: string;
    status: "success" | "failure" | "denied" | "cancelled" | "timeout";
    message: string;
  };
}
export interface SystemError extends EventBase {
  type: "system.error";
  payload: {
    code:
      | "invalid_message"
      | "unsupported_protocol"
      | "invalid_origin"
      | "message_too_large";
    message: string;
  };
}
export type ProtocolEvent =
  | ClientHello
  | SystemHealth
  | AssistantStateChanged
  | AudioTranscriptFinal
  | AssistantResponseStarted
  | AssistantResponseDelta
  | AssistantResponseCompleted
  | ModelStatusChanged
  | ToolExecutionStatus
  | ToolConfirmationRequested
  | ToolConfirmationResolved
  | ToolResult
  | SystemError;

export type RecordingKind =
  "positive" | "natural_command" | "hard_negative" | "ordinary_speech";

export interface NameAssessment {
  display_name: string;
  normalized_name: string;
  score: number;
  rating: "słaba" | "dobra" | "bardzo dobra";
  false_trigger_risk: "wysokie" | "umiarkowane" | "niskie";
  syllable_count: number;
  trainable: boolean;
  warnings: string[];
  explanation: string;
}

export interface RecordingStep {
  id: string;
  kind: RecordingKind;
  phrase: string;
  loudness: string;
  distance: string;
  intonation: string;
  posture: string;
  guidance: string;
  avoid: string;
  expected_seconds: [number, number];
}

export interface SampleQuality {
  accepted: boolean;
  speech_detected: boolean;
  volume_ok: boolean;
  no_clipping: boolean;
  duration_ok: boolean;
  silence_ok: boolean;
  signal_to_noise_db: number;
  rms: number;
  peak: number;
  clipping_ratio: number;
  silence_ratio: number;
  duration_seconds: number;
  reason: string;
}

export interface CalibrationResult {
  rms: number;
  peak: number;
  clipping_ratio: number;
  noise_floor_rms: number;
  ready: boolean;
  message: string;
}

export interface ValidationMetrics {
  attempts: number;
  successful_activations: number;
  missed_activations: number;
  false_accepts: number;
  recall: number;
  false_accepts_per_hour: number;
  threshold: number;
  positive_scores: number[];
  negative_scores: number[];
  passed: boolean;
}

export interface OnboardingSession {
  session_id: string;
  name: NameAssessment;
  microphone_device: string | number | null;
  keep_training_samples: boolean;
  curriculum: RecordingStep[];
  accepted_step_ids: string[];
  calibration: CalibrationResult | null;
  training_job_id: string | null;
  candidate_ready: boolean;
  validation: ValidationMetrics | null;
}

export interface TrainingJob {
  job_id: string;
  session_id: string;
  status: "running" | "ready" | "failed" | "cancelled";
  progress: number;
  stage: string;
  error: string | null;
}

export interface WakeModelMetadata {
  model_id: string;
  assistant_name: string;
  normalized_name: string;
  backend: "openwakeword-onnx";
  backend_version: string;
  model_version: number;
  trained_at: string;
  sensitivity: number;
  validated: boolean;
  validation: ValidationMetrics | null;
  microphone_device: string | number | null;
  keep_training_samples: boolean;
}

export function parseOnboardingSession(
  value: unknown,
): OnboardingSession | null {
  if (
    !isRecord(value) ||
    !isUuid(value.session_id) ||
    !Array.isArray(value.curriculum)
  )
    return null;
  if (!isNameAssessment(value.name)) return null;
  const curriculum = value.curriculum.filter(isRecordingStep);
  if (curriculum.length !== value.curriculum.length) return null;
  if (
    !Array.isArray(value.accepted_step_ids) ||
    !value.accepted_step_ids.every((item) => typeof item === "string") ||
    typeof value.keep_training_samples !== "boolean" ||
    typeof value.candidate_ready !== "boolean"
  )
    return null;
  return value as unknown as OnboardingSession;
}

export function parseTrainingJob(value: unknown): TrainingJob | null {
  if (
    !isRecord(value) ||
    !isUuid(value.job_id) ||
    !isUuid(value.session_id) ||
    !["running", "ready", "failed", "cancelled"].includes(
      String(value.status),
    ) ||
    !Number.isInteger(value.progress) ||
    Number(value.progress) < 0 ||
    Number(value.progress) > 100 ||
    typeof value.stage !== "string" ||
    !(value.error === null || typeof value.error === "string")
  )
    return null;
  return value as unknown as TrainingJob;
}

export function parseSampleQuality(value: unknown): SampleQuality | null {
  const booleans = [
    "accepted",
    "speech_detected",
    "volume_ok",
    "no_clipping",
    "duration_ok",
    "silence_ok",
  ];
  const numbers = [
    "signal_to_noise_db",
    "rms",
    "peak",
    "clipping_ratio",
    "silence_ratio",
    "duration_seconds",
  ];
  return isRecord(value) &&
    booleans.every((key) => typeof value[key] === "boolean") &&
    numbers.every((key) => typeof value[key] === "number") &&
    typeof value.reason === "string"
    ? (value as unknown as SampleQuality)
    : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNameAssessment(value: unknown): value is NameAssessment {
  return (
    isRecord(value) &&
    typeof value.display_name === "string" &&
    typeof value.normalized_name === "string" &&
    Number.isInteger(value.score) &&
    typeof value.rating === "string" &&
    typeof value.false_trigger_risk === "string" &&
    Number.isInteger(value.syllable_count) &&
    typeof value.trainable === "boolean" &&
    Array.isArray(value.warnings) &&
    value.warnings.every((item) => typeof item === "string") &&
    typeof value.explanation === "string"
  );
}

function isRecordingStep(value: unknown): value is RecordingStep {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    [
      "positive",
      "natural_command",
      "hard_negative",
      "ordinary_speech",
    ].includes(String(value.kind)) &&
    [
      "phrase",
      "loudness",
      "distance",
      "intonation",
      "posture",
      "guidance",
      "avoid",
    ].every((key) => typeof value[key] === "string") &&
    Array.isArray(value.expected_seconds) &&
    value.expected_seconds.length === 2 &&
    value.expected_seconds.every((item) => typeof item === "number")
  );
}

export function createClientHello(clientId: string): ClientHello {
  return {
    protocol_version: PROTOCOL_VERSION,
    event_id: crypto.randomUUID(),
    occurred_at: new Date().toISOString(),
    correlation_id: null,
    type: "client.hello",
    payload: { client_id: clientId, protocol_version: PROTOCOL_VERSION },
  };
}

export function parseProtocolEvent(value: unknown): ProtocolEvent | null {
  if (!isEnvelope(value)) return null;
  const payload = value.payload;
  switch (value.type) {
    case "client.hello":
      return exactObject(payload, ["client_id", "protocol_version"]) &&
        typeof payload.client_id === "string" &&
        codePointLength(payload.client_id) >= 1 &&
        codePointLength(payload.client_id) <= 128 &&
        payload.protocol_version === PROTOCOL_VERSION
        ? (value as unknown as ClientHello)
        : null;
    case "system.health":
      return parseHealth(payload) ? (value as unknown as SystemHealth) : null;
    case "assistant.state.changed":
      return exactObject(payload, ["previous_state", "state"]) &&
        isAssistantState(payload.state) &&
        (payload.previous_state === null ||
          isAssistantState(payload.previous_state))
        ? (value as unknown as AssistantStateChanged)
        : null;
    case "audio.transcript.final":
      return exactObject(payload, [
        "operation_id",
        "text",
        "language",
        "duration_ms",
      ]) &&
        isUuid(payload.operation_id) &&
        boundedString(payload.text, 1, 8_192) &&
        payload.language === "pl" &&
        Number.isInteger(payload.duration_ms) &&
        Number(payload.duration_ms) > 0 &&
        Number(payload.duration_ms) <= 120_000
        ? (value as unknown as AudioTranscriptFinal)
        : null;
    case "assistant.response.completed":
      return exactObject(payload, [
        "operation_id",
        "text",
        "spoken_text",
        "kind",
        "model",
      ]) &&
        isUuid(payload.operation_id) &&
        boundedString(payload.text, 1, 8_192) &&
        (payload.spoken_text === null ||
          boundedString(payload.spoken_text, 1, 1_024)) &&
        payload.kind === "local_model" &&
        boundedString(payload.model, 1, 128)
        ? (value as unknown as AssistantResponseCompleted)
        : null;
    case "assistant.response.started":
      return exactObject(payload, ["operation_id", "model", "mode"]) &&
        isUuid(payload.operation_id) &&
        boundedString(payload.model, 1, 128) &&
        (payload.mode === "voice" || payload.mode === "text")
        ? (value as unknown as AssistantResponseStarted)
        : null;
    case "assistant.response.delta":
      return exactObject(payload, ["operation_id", "sequence", "text"]) &&
        isUuid(payload.operation_id) &&
        Number.isInteger(payload.sequence) &&
        Number(payload.sequence) >= 0 &&
        Number(payload.sequence) <= 100_000 &&
        boundedString(payload.text, 1, 4_096)
        ? (value as unknown as AssistantResponseDelta)
        : null;
    case "model.status.changed":
      return exactObject(payload, ["provider", "model", "status", "detail"]) &&
        payload.provider === "ollama" &&
        boundedString(payload.model, 1, 128) &&
        modelStatuses.includes(
          payload.status as (typeof modelStatuses)[number],
        ) &&
        (payload.detail === null || boundedString(payload.detail, 1, 256))
        ? (value as unknown as ModelStatusChanged)
        : null;
    case "tool.execution.status":
      return exactObject(payload, [
        "operation_id",
        "call_id",
        "tool_name",
        "status",
      ]) &&
        isUuid(payload.operation_id) &&
        isUuid(payload.call_id) &&
        isToolName(payload.tool_name) &&
        ["requested", "confirmation_required", "executing"].includes(
          String(payload.status),
        )
        ? (value as unknown as ToolExecutionStatus)
        : null;
    case "tool.confirmation.requested":
      return exactObject(payload, [
        "confirmation_id",
        "operation_id",
        "call_id",
        "tool_name",
        "arguments_digest",
        "action",
        "target",
        "details",
        "risk",
        "expires_at",
        "persistent_allowed",
      ]) &&
        typeof payload.confirmation_id === "string" &&
        confirmationPattern.test(payload.confirmation_id) &&
        isUuid(payload.operation_id) &&
        isUuid(payload.call_id) &&
        isToolName(payload.tool_name) &&
        typeof payload.arguments_digest === "string" &&
        digestPattern.test(payload.arguments_digest) &&
        boundedString(payload.action, 1, 256) &&
        boundedString(payload.target, 1, 1_024) &&
        Array.isArray(payload.details) &&
        payload.details.length <= 12 &&
        payload.details.every(isConfirmationDetail) &&
        boundedString(payload.risk, 1, 512) &&
        isUtcTimestamp(payload.expires_at) &&
        typeof payload.persistent_allowed === "boolean"
        ? (value as unknown as ToolConfirmationRequested)
        : null;
    case "tool.confirmation.resolved":
      return exactObject(payload, [
        "confirmation_id",
        "operation_id",
        "call_id",
        "tool_name",
        "decision",
      ]) &&
        typeof payload.confirmation_id === "string" &&
        confirmationPattern.test(payload.confirmation_id) &&
        isUuid(payload.operation_id) &&
        isUuid(payload.call_id) &&
        isToolName(payload.tool_name) &&
        ["allow", "cancel", "always_allow"].includes(String(payload.decision))
        ? (value as unknown as ToolConfirmationResolved)
        : null;
    case "tool.result":
      return exactObject(payload, [
        "operation_id",
        "call_id",
        "tool_name",
        "status",
        "message",
      ]) &&
        isUuid(payload.operation_id) &&
        isUuid(payload.call_id) &&
        isToolName(payload.tool_name) &&
        ["success", "failure", "denied", "cancelled", "timeout"].includes(
          String(payload.status),
        ) &&
        boundedString(payload.message, 1, 512)
        ? (value as unknown as ToolResult)
        : null;
    case "system.error":
      return exactObject(payload, ["code", "message"]) &&
        errorCodes.includes(payload.code as (typeof errorCodes)[number]) &&
        boundedString(payload.message, 1, 256)
        ? (value as unknown as SystemError)
        : null;
    default:
      return null;
  }
}

export function parseHealth(value: unknown): SystemHealth["payload"] | null {
  return exactObject(value, [
    "service",
    "status",
    "protocol_version",
    "assistant_state",
  ]) &&
    value.service === "core" &&
    (value.status === "ready" || value.status === "stopping") &&
    value.protocol_version === PROTOCOL_VERSION &&
    isAssistantState(value.assistant_state)
    ? (value as unknown as SystemHealth["payload"])
    : null;
}

const uuidPattern =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const utcPattern =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|\+00:00)$/;
const errorCodes = [
  "invalid_message",
  "unsupported_protocol",
  "invalid_origin",
  "message_too_large",
] as const;
const modelStatuses = [
  "unavailable",
  "missing",
  "loading",
  "ready",
  "error",
] as const;
const confirmationPattern = /^[A-Za-z0-9_-]{32,128}$/;
const digestPattern = /^[0-9a-f]{64}$/;
const toolNamePattern = /^[a-z][a-z0-9_]{1,63}$/;

function exactObject(
  value: unknown,
  keys: readonly string[],
): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}

function isAssistantState(value: unknown): value is AssistantState {
  return (
    typeof value === "string" &&
    assistantStates.includes(value as AssistantState)
  );
}

function isToolName(value: unknown): value is string {
  return typeof value === "string" && toolNamePattern.test(value);
}

function isConfirmationDetail(value: unknown): value is ConfirmationDetail {
  return (
    exactObject(value, ["label", "value"]) &&
    boundedString(value.label, 1, 64) &&
    boundedString(value.value, 1, 1_024)
  );
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" && uuidPattern.test(value);
}

function boundedString(
  value: unknown,
  minimum: number,
  maximum: number,
): value is string {
  if (typeof value !== "string") return false;
  const length = codePointLength(value);
  return length >= minimum && length <= maximum;
}

function codePointLength(value: string): number {
  return Array.from(value).length;
}

function isUtcTimestamp(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = utcPattern.exec(value);
  if (!match) return false;
  const parsed = new Date(value);
  return (
    !Number.isNaN(parsed.valueOf()) &&
    parsed.getUTCFullYear() === Number(match[1]) &&
    parsed.getUTCMonth() + 1 === Number(match[2]) &&
    parsed.getUTCDate() === Number(match[3]) &&
    parsed.getUTCHours() === Number(match[4]) &&
    parsed.getUTCMinutes() === Number(match[5]) &&
    parsed.getUTCSeconds() === Number(match[6])
  );
}

function isEnvelope(value: unknown): value is Record<string, unknown> & {
  type: string;
  payload: Record<string, unknown>;
} {
  return (
    exactObject(value, [
      "protocol_version",
      "event_id",
      "occurred_at",
      "correlation_id",
      "type",
      "payload",
    ]) &&
    value.protocol_version === PROTOCOL_VERSION &&
    isUuid(value.event_id) &&
    isUtcTimestamp(value.occurred_at) &&
    (value.correlation_id === null || isUuid(value.correlation_id)) &&
    typeof value.type === "string" &&
    typeof value.payload === "object" &&
    value.payload !== null &&
    !Array.isArray(value.payload)
  );
}
