import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { connectToCore } from "./coreClient";
import type { ClientHello, AssistantState } from "@moj-asystent/protocol";

class FakeSocket extends EventTarget {
  static instances: FakeSocket[] = [];
  sent: string[] = [];
  close = vi.fn();
  constructor() {
    super();
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  open() {
    this.dispatchEvent(new Event("open"));
  }
  frame(data: unknown) {
    const event = new Event("message");
    Object.defineProperty(event, "data", {
      value: typeof data === "string" ? data : JSON.stringify(data),
    });
    this.dispatchEvent(event);
  }
  get hello(): ClientHello {
    return JSON.parse(this.sent[0]);
  }
}
const health = {
  service: "core",
  status: "ready",
  protocol_version: "1.0",
  assistant_state: "idle",
};
const response = () => ({
  ok: true,
  text: async () => JSON.stringify(health),
});
const wire = (
  type: string,
  payload: unknown,
  correlation_id: string | null = null,
) => ({
  protocol_version: "1.0",
  event_id: crypto.randomUUID(),
  occurred_at: new Date().toISOString(),
  correlation_id,
  type,
  payload,
});
function synchronize(socket: FakeSocket, state: AssistantState = "idle") {
  socket.open();
  socket.frame(wire("system.health", health, socket.hello.event_id));
  socket.frame(
    wire(
      "assistant.state.changed",
      { previous_state: null, state },
      socket.hello.event_id,
    ),
  );
}
let request: ReturnType<typeof vi.fn>;
let stop: (() => void) | undefined;
beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  request = vi.fn().mockImplementation(response);
  vi.stubGlobal("fetch", request);
});
afterEach(() => {
  stop?.();
  stop = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("connects if the core starts after the desktop, with capped exponential backoff", async () => {
  request.mockRejectedValue(new Error("offline"));
  const status = vi.fn();
  stop = connectToCore(status, vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  expect(request).toHaveBeenCalledTimes(1);
  for (const [index, delay] of [
    500, 1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000,
  ].entries()) {
    await vi.advanceTimersByTimeAsync(delay - 1);
    expect(request).toHaveBeenCalledTimes(index + 1);
    await vi.advanceTimersByTimeAsync(1);
    expect(request).toHaveBeenCalledTimes(index + 2);
  }
  request.mockImplementation(response);
  await vi.advanceTimersByTimeAsync(30_000);
  synchronize(FakeSocket.instances[0]);
  expect(status).toHaveBeenLastCalledWith("connected");
});

it("reconnects after a crash and ignores every callback from the old connection", async () => {
  const state = vi.fn();
  const status = vi.fn();
  stop = connectToCore(status, state);
  await vi.advanceTimersByTimeAsync(0);
  const old = FakeSocket.instances[0];
  synchronize(old, "speaking");
  old.dispatchEvent(new Event("error"));
  old.dispatchEvent(new Event("close"));
  await vi.advanceTimersByTimeAsync(500);
  const current = FakeSocket.instances[1];
  synchronize(current, "thinking");
  const count = state.mock.calls.length;
  old.open();
  old.frame(
    wire("assistant.state.changed", {
      previous_state: "speaking",
      state: "idle",
    }),
  );
  old.dispatchEvent(new Event("error"));
  expect(old.sent).toHaveLength(1);
  expect(state).toHaveBeenCalledTimes(count);
  expect(state).toHaveBeenLastCalledWith("thinking");
  expect(status).toHaveBeenLastCalledWith("connected");
  expect(FakeSocket.instances).toHaveLength(2);
});

it("cancels pending HTTP work and suppresses its late result on disposal", async () => {
  let resolve!: (value: unknown) => void;
  request.mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const status = vi.fn();
  stop = connectToCore(status, vi.fn());
  const signal = request.mock.calls[0][1].signal as AbortSignal;
  stop();
  expect(signal.aborted).toBe(true);
  resolve(response());
  await vi.advanceTimersByTimeAsync(100_000);
  expect(FakeSocket.instances).toHaveLength(0);
  expect(request).toHaveBeenCalledTimes(1);
  expect(status).toHaveBeenCalledTimes(1);
});

it("disposes sockets and timers with no subsequent state callbacks", async () => {
  const state = vi.fn();
  stop = connectToCore(vi.fn(), state);
  await vi.advanceTimersByTimeAsync(0);
  const socket = FakeSocket.instances[0];
  synchronize(socket);
  stop();
  socket.frame(
    wire("assistant.state.changed", {
      previous_state: "idle",
      state: "listening",
    }),
  );
  await vi.advanceTimersByTimeAsync(100_000);
  expect(socket.close).toHaveBeenCalled();
  expect(state).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

it.each(["http", "socket", "snapshot"])(
  "times out a stalled %s handshake",
  async (phase) => {
    if (phase === "http") request.mockReturnValue(new Promise(() => {}));
    const status = vi.fn();
    stop = connectToCore(status, vi.fn());
    await vi.advanceTimersByTimeAsync(0);
    if (phase === "snapshot") {
      const socket = FakeSocket.instances[0];
      socket.open();
      socket.frame(wire("system.health", health, socket.hello.event_id));
    }
    await vi.advanceTimersByTimeAsync(5_000);
    expect(status).toHaveBeenLastCalledWith("disconnected");
    await vi.advanceTimersByTimeAsync(500);
    expect(request).toHaveBeenCalledTimes(2);
  },
);

it("requires correlated health and snapshot before connected status", async () => {
  const status = vi.fn();
  stop = connectToCore(status, vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  const socket = FakeSocket.instances[0];
  socket.open();
  socket.frame(wire("system.health", health, socket.hello.event_id));
  expect(status).not.toHaveBeenCalledWith("connected");
  socket.frame(
    wire(
      "assistant.state.changed",
      { previous_state: null, state: "thinking" },
      socket.hello.event_id,
    ),
  );
  expect(status).toHaveBeenLastCalledWith("connected");
});

it.each([
  "malformed",
  "uncorrelated",
  "uncorrelated-state",
  "out-of-order",
  "duplicate",
  "stopping",
  "oversized",
])("rejects %s events and reconnects", async (scenario) => {
  const state = vi.fn();
  const status = vi.fn();
  stop = connectToCore(status, state);
  await vi.advanceTimersByTimeAsync(0);
  const socket = FakeSocket.instances[0];
  synchronize(socket);
  if (scenario === "malformed") socket.frame("{");
  if (scenario === "uncorrelated")
    socket.frame(wire("system.health", health, crypto.randomUUID()));
  if (scenario === "uncorrelated-state")
    socket.frame(
      wire(
        "assistant.state.changed",
        { previous_state: "idle", state: "listening" },
        crypto.randomUUID(),
      ),
    );
  if (scenario === "out-of-order")
    socket.frame(
      wire(
        "assistant.state.changed",
        { previous_state: "speaking", state: "idle" },
        socket.hello.event_id,
      ),
    );
  if (scenario === "stopping")
    socket.frame(
      wire(
        "system.health",
        { ...health, status: "stopping" },
        socket.hello.event_id,
      ),
    );
  if (scenario === "oversized") socket.frame("x".repeat(32_769));
  if (scenario === "duplicate") {
    const event = wire(
      "assistant.state.changed",
      { previous_state: "idle", state: "listening" },
      socket.hello.event_id,
    );
    socket.frame(event);
    socket.frame(event);
  }
  expect(status).toHaveBeenLastCalledWith("disconnected");
  expect(socket.close).toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(500);
  expect(FakeSocket.instances).toHaveLength(2);
});

it("detects a silent core and refreshes the liveness deadline on heartbeat", async () => {
  const status = vi.fn();
  stop = connectToCore(status, vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  const socket = FakeSocket.instances[0];
  synchronize(socket);
  await vi.advanceTimersByTimeAsync(20_000);
  socket.frame(wire("system.health", health, socket.hello.event_id));
  await vi.advanceTimersByTimeAsync(20_000);
  expect(status).toHaveBeenLastCalledWith("connected");
  await vi.advanceTimersByTimeAsync(5_000);
  expect(status).toHaveBeenLastCalledWith("disconnected");
});

it("validates HTTP health before opening a socket", async () => {
  request.mockResolvedValue({
    ok: true,
    text: async () => JSON.stringify({ ...health, protocol_version: "1.1" }),
  });
  const status = vi.fn();
  stop = connectToCore(status, vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  expect(FakeSocket.instances).toHaveLength(0);
  expect(status).toHaveBeenLastCalledWith("disconnected");
});

it("rejects an oversized HTTP health response", async () => {
  request.mockResolvedValue({
    ok: true,
    text: async () => "x".repeat(32_769),
  });
  const status = vi.fn();
  stop = connectToCore(status, vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  expect(FakeSocket.instances).toHaveLength(0);
  expect(status).toHaveBeenLastCalledWith("disconnected");
});

it.each([
  "http://example.com",
  "http://127.0.0.1.evil",
  "https://127.0.0.1",
  "http://user:pass@localhost",
])("rejects a non-local or credentialed URL: %s", (url) => {
  expect(() => connectToCore(vi.fn(), vi.fn(), url)).toThrow();
  expect(request).not.toHaveBeenCalled();
});
