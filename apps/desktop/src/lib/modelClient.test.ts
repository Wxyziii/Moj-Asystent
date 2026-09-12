import { afterEach, expect, it, vi } from "vitest";
import {
  getModelCatalog,
  getModelSettings,
  updateModelSettings,
} from "./modelClient";

const credential = "R".repeat(43);

afterEach(() => vi.unstubAllGlobals());

it("reads and validates model settings from the local core", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        mode: "auto",
        data_policy: "local_only",
        effective_data_policy: "local_only",
      }),
    }),
  );

  await expect(getModelSettings(credential)).resolves.toEqual({
    mode: "auto",
    data_policy: "local_only",
    effective_data_policy: "local_only",
  });
});

it("persists a mode through the exact typed protocol request", async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      mode: "quality",
      data_policy: "local_only",
      effective_data_policy: "local_only",
    }),
  });
  vi.stubGlobal("fetch", fetch);

  await updateModelSettings(credential, { mode: "quality" });

  expect(fetch).toHaveBeenCalledWith(
    "http://127.0.0.1:8765/model/settings",
    expect.objectContaining({
      method: "PATCH",
      redirect: "error",
      body: JSON.stringify({ protocol_version: "1.4", mode: "quality" }),
    }),
  );
});

it("rejects a malformed provider catalog", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ models: [{ provider: "arbitrary-url" }] }),
    }),
  );

  await expect(getModelCatalog(credential)).rejects.toThrow("katalog");
});
