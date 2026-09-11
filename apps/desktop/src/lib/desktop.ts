import { invoke } from "@tauri-apps/api/core";
import type { ToolConfirmationRequested } from "@moj-asystent/protocol";

export type ToolConfirmationDecision = "allow" | "always_allow" | "cancel";

export async function requestDesktopMode(
  mode: "compact" | "expanded" | "settings",
) {
  try {
    await invoke("set_overlay_mode", { mode });
  } catch {
    // The browser preview has no Tauri bridge. The UI still works there.
  }
}

export async function hideOverlay() {
  try {
    await invoke("hide_overlay");
  } catch {
    // No-op in browser preview.
  }
}

export async function getCoreSessionCredential(): Promise<string> {
  return invoke<string>("get_core_session_credential");
}

export async function resolveToolConfirmation(
  request: ToolConfirmationRequested["payload"],
  decision: ToolConfirmationDecision,
): Promise<void> {
  await invoke("resolve_tool_confirmation", {
    decision: {
      confirmation_id: request.confirmation_id,
      operation_id: request.operation_id,
      call_id: request.call_id,
      tool_name: request.tool_name,
      arguments_digest: request.arguments_digest,
      decision,
    },
  });
}
