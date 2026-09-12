import { PROTOCOL_VERSION } from "@moj-asystent/protocol";

export type ModelMode = "private" | "fast" | "quality" | "deep" | "auto";
export type ModelDataPolicy = "local_only" | "cloud_allowed";

export interface ModelSettings {
  mode: ModelMode;
  data_policy: ModelDataPolicy;
  effective_data_policy: ModelDataPolicy;
}

export interface ModelCatalogEntry {
  tier: "fast" | "quality" | "deep";
  provider: "ollama" | "llama_cpp" | "openrouter";
  model: string;
  location: "local" | "cloud";
  capabilities: {
    text: boolean;
    image: boolean;
    structured_output: boolean;
    tools: boolean;
    context_size: number;
  };
  approximate_size_bytes: number | null;
  status: "unavailable" | "missing" | "loading" | "ready" | "error";
  detail: string | null;
  resident: boolean;
}

const defaultUrl = import.meta.env.VITE_CORE_URL ?? "http://127.0.0.1:8765";
const credentialPattern = /^[A-Za-z0-9_-]{43,128}$/;

export async function getModelSettings(
  credential: string,
  baseUrl = defaultUrl,
): Promise<ModelSettings> {
  return parseSettings(
    await requestJson("/model/settings", credential, baseUrl),
  );
}

export async function updateModelSettings(
  credential: string,
  update: { mode?: ModelMode; data_policy?: ModelDataPolicy },
  baseUrl = defaultUrl,
): Promise<ModelSettings> {
  if (update.mode === undefined && update.data_policy === undefined)
    throw new Error("Wybierz ustawienie modelu do zmiany.");
  return parseSettings(
    await requestJson("/model/settings", credential, baseUrl, {
      method: "PATCH",
      body: JSON.stringify({ protocol_version: PROTOCOL_VERSION, ...update }),
    }),
  );
}

export async function getModelCatalog(
  credential: string,
  baseUrl = defaultUrl,
): Promise<ModelCatalogEntry[]> {
  const value = await requestJson("/models/catalog", credential, baseUrl);
  if (
    !isRecord(value) ||
    !Array.isArray(value.models) ||
    !value.models.every(isCatalogEntry)
  )
    throw new Error("Rdzeń zwrócił nieprawidłowy katalog modeli.");
  return value.models;
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
        : "Core odrzucił zmianę modelu.";
    throw new Error(detail);
  }
  return value;
}

function parseSettings(value: unknown): ModelSettings {
  if (
    !isRecord(value) ||
    !isMode(value.mode) ||
    !isPolicy(value.data_policy) ||
    !isPolicy(value.effective_data_policy)
  )
    throw new Error("Rdzeń zwrócił nieprawidłowe ustawienia modelu.");
  return {
    mode: value.mode,
    data_policy: value.data_policy,
    effective_data_policy: value.effective_data_policy,
  };
}

function isCatalogEntry(value: unknown): value is ModelCatalogEntry {
  if (!isRecord(value) || !isRecord(value.capabilities)) return false;
  const capabilities = value.capabilities;
  return (
    ["fast", "quality", "deep"].includes(String(value.tier)) &&
    ["ollama", "llama_cpp", "openrouter"].includes(String(value.provider)) &&
    ["local", "cloud"].includes(String(value.location)) &&
    typeof value.model === "string" &&
    value.model.length > 0 &&
    value.model.length <= 128 &&
    ["unavailable", "missing", "loading", "ready", "error"].includes(
      String(value.status),
    ) &&
    (value.detail === null || typeof value.detail === "string") &&
    (value.approximate_size_bytes === null ||
      (typeof value.approximate_size_bytes === "number" &&
        value.approximate_size_bytes > 0)) &&
    typeof value.resident === "boolean" &&
    typeof capabilities.text === "boolean" &&
    typeof capabilities.image === "boolean" &&
    typeof capabilities.structured_output === "boolean" &&
    typeof capabilities.tools === "boolean" &&
    Number.isInteger(capabilities.context_size) &&
    Number(capabilities.context_size) >= 1_024
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

function isMode(value: unknown): value is ModelMode {
  return ["private", "fast", "quality", "deep", "auto"].includes(String(value));
}

function isPolicy(value: unknown): value is ModelDataPolicy {
  return value === "local_only" || value === "cloud_allowed";
}
