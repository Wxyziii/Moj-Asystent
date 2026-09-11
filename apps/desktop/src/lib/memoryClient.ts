import { PROTOCOL_VERSION } from "@moj-asystent/protocol";

const coreOrigin = import.meta.env.VITE_CORE_URL ?? "http://127.0.0.1:8765";

export interface MemorySettings {
  history_retention: boolean;
}

export interface MemoryRecord {
  memory_id: string;
  category: "preference" | "memory";
  key: string;
  value: string;
  source: string;
  approved: boolean;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
}

export interface AliasRecord {
  alias_id: string;
  kind: "app" | "project";
  alias: string;
  target: string;
  updated_at: string;
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseSettings(value: unknown): MemorySettings {
  if (!isRecord(value) || typeof value.history_retention !== "boolean")
    throw new Error("Core zwrócił nieprawidłowe ustawienia pamięci.");
  return { history_retention: value.history_retention };
}

function parseMemories(value: unknown): MemoryRecord[] {
  if (!isRecord(value) || !Array.isArray(value.memories))
    throw new Error("Core zwrócił nieprawidłową listę pamięci.");
  return value.memories.filter(isRecord).flatMap((item): MemoryRecord[] => {
    if (
      typeof item.memory_id !== "string" ||
      (item.category !== "memory" && item.category !== "preference") ||
      typeof item.key !== "string" ||
      typeof item.value !== "string" ||
      typeof item.source !== "string" ||
      typeof item.updated_at !== "string"
    )
      return [];
    return [
      {
        memory_id: item.memory_id,
        category: item.category,
        key: item.key,
        value: item.value,
        source: item.source,
        approved: item.approved === true,
        created_at:
          typeof item.created_at === "string"
            ? item.created_at
            : item.updated_at,
        updated_at: item.updated_at,
        expires_at:
          typeof item.expires_at === "string" ? item.expires_at : null,
      },
    ];
  });
}

export async function getMemorySettings(
  credential: string,
): Promise<MemorySettings> {
  return parseSettings(await request("/memory/settings", credential));
}

export async function setHistoryRetention(
  credential: string,
  enabled: boolean,
): Promise<MemorySettings> {
  return parseSettings(
    await request("/memory/settings", credential, {
      method: "PATCH",
      body: JSON.stringify({ protocol_version: PROTOCOL_VERSION, enabled }),
    }),
  );
}

export async function listMemories(
  credential: string,
): Promise<MemoryRecord[]> {
  return parseMemories(await request("/memories?limit=12", credential));
}

export async function listAliases(credential: string): Promise<AliasRecord[]> {
  const value = await request("/aliases", credential);
  if (!isRecord(value) || !Array.isArray(value.aliases))
    throw new Error("Core zwrócił nieprawidłową listę aliasów.");
  return value.aliases.filter(isRecord).flatMap((item): AliasRecord[] => {
    if (
      typeof item.alias_id !== "string" ||
      (item.kind !== "app" && item.kind !== "project") ||
      typeof item.alias !== "string" ||
      typeof item.target !== "string" ||
      typeof item.updated_at !== "string"
    )
      return [];
    return [
      {
        alias_id: item.alias_id,
        kind: item.kind,
        alias: item.alias,
        target: item.target,
        updated_at: item.updated_at,
      },
    ];
  });
}

export async function deleteMemory(
  credential: string,
  memoryId: string,
): Promise<boolean> {
  const value = await request(
    `/memories/${encodeURIComponent(memoryId)}`,
    credential,
    { method: "DELETE" },
  );
  return isRecord(value) && value.removed === true;
}

export async function deleteAlias(
  credential: string,
  aliasId: string,
): Promise<boolean> {
  const value = await request(
    `/aliases/${encodeURIComponent(aliasId)}`,
    credential,
    {
      method: "DELETE",
    },
  );
  return isRecord(value) && value.removed === true;
}

export async function clearHistory(credential: string): Promise<number> {
  const value = await request("/history", credential, { method: "DELETE" });
  return isRecord(value) && typeof value.removed === "number"
    ? value.removed
    : 0;
}

export async function clearMemories(credential: string): Promise<number> {
  const value = await request("/memory/clear", credential, {
    method: "POST",
    body: JSON.stringify({ protocol_version: PROTOCOL_VERSION, confirm: true }),
  });
  return isRecord(value) && typeof value.removed === "number"
    ? value.removed
    : 0;
}
