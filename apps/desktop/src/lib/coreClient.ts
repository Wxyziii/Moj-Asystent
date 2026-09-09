import {
  createClientHello,
  parseProtocolEvent,
  type AssistantState,
} from "@moj-asystent/protocol";

export type CoreConnectionStatus = "connecting" | "connected" | "disconnected";
const coreUrl = import.meta.env.VITE_CORE_URL ?? "http://127.0.0.1:8765";

export function connectToCore(
  onStatus: (status: CoreConnectionStatus) => void,
  onState: (state: AssistantState) => void,
): () => void {
  let disposed = false;
  let socket: WebSocket | undefined;
  const stop = () => {
    disposed = true;
    socket?.close();
  };
  void (async () => {
    onStatus("connecting");
    try {
      const health = await fetch(`${coreUrl}/health`, {
        signal: AbortSignal.timeout(2_000),
      });
      if (!health.ok) throw new Error("Core health endpoint unavailable");
      if (disposed) return;
      socket = new WebSocket(coreUrl.replace(/^http/, "ws") + "/ws");
      socket.addEventListener("open", () =>
        socket?.send(JSON.stringify(createClientHello("desktop-overlay"))),
      );
      socket.addEventListener("message", (message) => {
        let event: ReturnType<typeof parseProtocolEvent>;
        try {
          event = parseProtocolEvent(JSON.parse(String(message.data)));
        } catch {
          onStatus("disconnected");
          return;
        }
        if (!event) {
          onStatus("disconnected");
          return;
        }
        if (event.type === "system.health") onStatus("connected");
        if (event.type === "assistant.state.changed")
          onState(event.payload.state);
        if (event.type === "system.error") onStatus("disconnected");
      });
      socket.addEventListener(
        "close",
        () => !disposed && onStatus("disconnected"),
      );
      socket.addEventListener(
        "error",
        () => !disposed && onStatus("disconnected"),
      );
    } catch {
      if (!disposed) onStatus("disconnected");
    }
  })();
  return stop;
}
