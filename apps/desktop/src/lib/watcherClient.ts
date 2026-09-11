import { PROTOCOL_VERSION } from "@moj-asystent/protocol";

export type WatcherType =
  "window" | "process" | "file" | "resource" | "build" | "download";
export type WatcherStatus =
  "active" | "paused" | "completed" | "failed" | "cancelled" | "expired";
export interface WatcherRecord {
  watcher_id: string;
  watcher_type: WatcherType;
  status: WatcherStatus;
  name: string;
  target: Record<string, unknown>;
  condition: Record<string, unknown>;
  interval_seconds: number;
  expires_at: string | null;
  one_shot: boolean;
  notification_level: "normal" | "quiet";
  last_observed: Record<string, unknown> | null;
  last_event_type: string | null;
  created_at: string;
  updated_at: string;
}

const defaultUrl = import.meta.env.VITE_CORE_URL ?? "http://127.0.0.1:8765";
const credentialPattern = /^[A-Za-z0-9_-]{43,128}$/;
const uuidPattern =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

export async function listWatchers(
  credential: string,
  baseUrl = defaultUrl,
): Promise<WatcherRecord[]> {
  const value = await requestJson(
    `${validateUrl(baseUrl).origin}/watchers`,
    credential,
  );
  if (
    !isRecord(value) ||
    !Array.isArray(value.watchers) ||
    !value.watchers.every(isWatcher)
  )
    throw new Error("Rdzeń zwrócił nieprawidłową listę obserwacji.");
  return value.watchers;
}

export async function pauseWatcher(
  watcherId: string,
  credential: string,
  baseUrl = defaultUrl,
): Promise<WatcherRecord> {
  return watcherAction(watcherId, "pause", credential, baseUrl);
}

export async function resumeWatcher(
  watcherId: string,
  credential: string,
  baseUrl = defaultUrl,
): Promise<WatcherRecord> {
  return watcherAction(watcherId, "resume", credential, baseUrl);
}

export async function cancelWatcher(
  watcherId: string,
  credential: string,
  baseUrl = defaultUrl,
): Promise<WatcherRecord> {
  return watcherAction(watcherId, "cancel", credential, baseUrl);
}

export async function deleteWatcher(
  watcherId: string,
  credential: string,
  baseUrl = defaultUrl,
): Promise<boolean> {
  if (!uuidPattern.test(watcherId) || !credentialPattern.test(credential))
    throw new Error("Nieprawidłowa obserwacja.");
  const response = await fetch(
    `${validateUrl(baseUrl).origin}/watchers/${watcherId}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${credential}` },
      redirect: "error",
    },
  );
  const value = await response.json().catch(() => null);
  if (!response.ok || !isRecord(value) || typeof value.removed !== "boolean")
    throw new Error("Nie udało się usunąć obserwacji.");
  return value.removed;
}

async function watcherAction(
  watcherId: string,
  action: "pause" | "resume" | "cancel",
  credential: string,
  baseUrl: string,
): Promise<WatcherRecord> {
  if (!uuidPattern.test(watcherId) || !credentialPattern.test(credential))
    throw new Error("Nieprawidłowa obserwacja.");
  const value = await requestJson(
    `${validateUrl(baseUrl).origin}/watchers/${watcherId}/${action}`,
    credential,
    "POST",
  );
  if (!isWatcher(value))
    throw new Error("Rdzeń zwrócił nieprawidłową obserwację.");
  return value;
}

async function requestJson(
  url: string,
  credential: string,
  method = "GET",
): Promise<unknown> {
  if (!credentialPattern.test(credential))
    throw new Error("Invalid credential");
  const response = await fetch(url, {
    method,
    headers: {
      Authorization: `Bearer ${credential}`,
      ...(method !== "GET" ? { "Content-Type": "application/json" } : {}),
    },
    redirect: "error",
  });
  const value = await response.json().catch(() => null);
  if (!response.ok) throw new Error("Rdzeń odrzucił operację obserwacji.");
  return value;
}

function isWatcher(value: unknown): value is WatcherRecord {
  return (
    isRecord(value) &&
    typeof value.watcher_id === "string" &&
    uuidPattern.test(value.watcher_id) &&
    ["window", "process", "file", "resource", "build", "download"].includes(
      String(value.watcher_type),
    ) &&
    [
      "active",
      "paused",
      "completed",
      "failed",
      "cancelled",
      "expired",
    ].includes(String(value.status)) &&
    typeof value.name === "string" &&
    isRecord(value.target) &&
    isRecord(value.condition) &&
    typeof value.interval_seconds === "number" &&
    typeof value.one_shot === "boolean" &&
    (value.expires_at === null || typeof value.expires_at === "string") &&
    (value.last_observed === null || isRecord(value.last_observed)) &&
    (value.last_event_type === null ||
      typeof value.last_event_type === "string") &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validateUrl(baseUrl: string): URL {
  const url = new URL(baseUrl);
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "[::1]", "localhost"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  )
    throw new Error("Core URL must be a local HTTP origin");
  return url;
}

export const watcherProtocolVersion = PROTOCOL_VERSION;
