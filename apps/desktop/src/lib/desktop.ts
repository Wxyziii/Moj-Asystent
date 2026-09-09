import { invoke } from "@tauri-apps/api/core";

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
