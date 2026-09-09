export const PROTOCOL_VERSION = "1.0" as const;

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
  ClientHello | SystemHealth | AssistantStateChanged | SystemError;

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
  if (!value || typeof value !== "object") return null;
  const event = value as Partial<ProtocolEvent>;
  if (
    event.protocol_version !== PROTOCOL_VERSION ||
    typeof event.type !== "string" ||
    !event.payload ||
    typeof event.payload !== "object"
  )
    return null;
  if (event.type === "system.health") {
    const payload = event.payload as SystemHealth["payload"];
    return payload.service === "core" &&
      (payload.status === "ready" || payload.status === "stopping") &&
      assistantStates.includes(payload.assistant_state)
      ? (event as SystemHealth)
      : null;
  }
  if (event.type === "assistant.state.changed") {
    const payload = event.payload as AssistantStateChanged["payload"];
    return assistantStates.includes(payload.state) &&
      (payload.previous_state === null ||
        assistantStates.includes(payload.previous_state))
      ? (event as AssistantStateChanged)
      : null;
  }
  if (event.type === "system.error") return event as SystemError;
  if (event.type === "client.hello") return event as ClientHello;
  return null;
}
