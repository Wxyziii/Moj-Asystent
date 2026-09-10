import {
  createClientHello,
  parseHealth,
  parseProtocolEvent,
  type AssistantState,
} from "@moj-asystent/protocol";

export type CoreConnectionStatus = "connecting" | "connected" | "disconnected";
const defaultUrl = import.meta.env.VITE_CORE_URL ?? "http://127.0.0.1:8765";
const HANDSHAKE_TIMEOUT = 5_000;
const LIVENESS_TIMEOUT = 25_000;
const MAX_MESSAGE_BYTES = 32_768;

/** One owned connection at a time; every callback is scoped to its attempt. */
export function connectToCore(
  onStatus: (status: CoreConnectionStatus) => void,
  onState: (state: AssistantState) => void,
  baseUrl = defaultUrl,
): () => void {
  const url = new URL(baseUrl);
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "[::1]", "localhost"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("Core URL must be a local HTTP origin");
  }
  let disposed = false;
  let attempt = 0;
  let failures = 0;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let socket: WebSocket | undefined;

  const cleanup = () => {
    clearTimeout(deadline);
    controller?.abort();
    controller = undefined;
    const previous = socket;
    socket = undefined;
    previous?.close();
  };
  const start = async () => {
    if (disposed) return;
    const current = ++attempt;
    const active = () => !disposed && current === attempt;
    const fail = () => {
      if (!active()) return;
      ++attempt; // Invalidate old frames, errors and pending health responses first.
      cleanup();
      onStatus("disconnected");
      const delay = Math.min(30_000, 500 * 2 ** Math.min(failures++, 6));
      retry = setTimeout(() => void start(), delay);
    };
    const armDeadline = (milliseconds: number) => {
      clearTimeout(deadline);
      deadline = setTimeout(fail, milliseconds);
    };
    onStatus("connecting");
    controller = new AbortController();
    armDeadline(HANDSHAKE_TIMEOUT);
    try {
      const response = await fetch(url.origin + "/health", {
        signal: controller.signal,
        redirect: "error",
        cache: "no-store",
      });
      if (!active()) return;
      const body = await response.text();
      if (!active()) return;
      if (new TextEncoder().encode(body).length > MAX_MESSAGE_BYTES) {
        fail();
        return;
      }
      const health = parseHealth(JSON.parse(body));
      if (!response.ok || health?.status !== "ready") {
        fail();
        return;
      }
      const connection = new WebSocket(
        url.origin.replace(/^http:/, "ws:") + "/ws",
      );
      socket = connection;
      const hello = createClientHello("desktop-overlay");
      let phase: "health" | "snapshot" | "ready" = "health";
      let previousState: AssistantState | undefined;
      const seen = new Set<string>();
      connection.addEventListener("open", () => {
        if (active()) connection.send(JSON.stringify(hello));
      });
      connection.addEventListener("message", (message) => {
        if (!active()) return;
        try {
          if (
            typeof message.data !== "string" ||
            new TextEncoder().encode(message.data).length > MAX_MESSAGE_BYTES
          ) {
            fail();
            return;
          }
          const event = parseProtocolEvent(JSON.parse(message.data));
          if (!event || seen.has(event.event_id)) {
            fail();
            return;
          }
          seen.add(event.event_id);
          if (seen.size > 256) seen.delete(seen.values().next().value!);
          if (event.type === "system.health") {
            if (
              event.correlation_id !== hello.event_id ||
              event.payload.status !== "ready" ||
              phase === "snapshot"
            ) {
              fail();
              return;
            }
            if (phase === "health") phase = "snapshot";
            else armDeadline(LIVENESS_TIMEOUT);
            return;
          }
          if (
            event.type !== "assistant.state.changed" ||
            phase === "health" ||
            event.correlation_id !== hello.event_id ||
            (phase === "snapshot" && event.payload.previous_state !== null) ||
            (phase === "ready" &&
              event.payload.previous_state !== previousState)
          ) {
            fail();
            return;
          }
          previousState = event.payload.state;
          onState(event.payload.state); // Snapshot is authoritative, including after restart.
          if (phase === "snapshot") {
            phase = "ready";
            failures = 0;
            onStatus("connected");
          }
          armDeadline(LIVENESS_TIMEOUT);
        } catch {
          fail();
        }
      });
      connection.addEventListener("close", fail);
      connection.addEventListener("error", fail);
    } catch {
      fail();
    }
  };
  void start();
  return () => {
    disposed = true;
    ++attempt;
    clearTimeout(retry);
    cleanup();
  };
}
