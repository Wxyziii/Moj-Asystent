import { beforeEach, expect, it, vi } from "vitest";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { resolveToolConfirmation } from "./desktop";
import type { ToolConfirmationRequested } from "@moj-asystent/protocol";

beforeEach(() => invoke.mockReset());

it("passes only the exact confirmation binding to the privileged Rust command", async () => {
  invoke.mockResolvedValue(undefined);
  const request: ToolConfirmationRequested["payload"] = {
    confirmation_id: "a".repeat(43),
    operation_id: crypto.randomUUID(),
    call_id: crypto.randomUUID(),
    tool_name: "file_delete",
    arguments_digest: "b".repeat(64),
    action: "Usunąć plik?",
    target: "C:\\Users\\Test\\notes.txt",
    details: [{ label: "Plik", value: "notes.txt" }],
    risk: "Tej czynności nie można cofnąć.",
    expires_at: "2026-09-11T08:00:00Z",
    persistent_allowed: false,
  };

  await resolveToolConfirmation(request, "allow");

  expect(invoke).toHaveBeenCalledWith("resolve_tool_confirmation", {
    decision: {
      confirmation_id: request.confirmation_id,
      operation_id: request.operation_id,
      call_id: request.call_id,
      tool_name: request.tool_name,
      arguments_digest: request.arguments_digest,
      decision: "allow",
    },
  });
});
