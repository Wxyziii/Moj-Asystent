import { useCallback, useEffect } from "react";
import { listen } from "@tauri-apps/api/event";
import { CompactOverlay } from "./components/CompactOverlay";
import { Conversation } from "./components/Conversation";
import { SettingsShell } from "./components/SettingsShell";
import {
  assistantStateDefinitions,
  type OverlayMode,
} from "./domain/assistant";
import { useAssistantUi } from "./hooks/useAssistant";
import { hideOverlay, requestDesktopMode } from "./lib/desktop";

export default function App() {
  const {
    assistantState,
    coreStatus,
    overlayMode,
    setOverlayMode,
    transitionTo,
    previewState,
  } = useAssistantUi();
  const definition = assistantStateDefinitions[assistantState];

  const changeMode = useCallback(
    (mode: OverlayMode) => {
      setOverlayMode(mode);
      void requestDesktopMode(mode);
    },
    [setOverlayMode],
  );

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void listen<string>("overlay-command", (event) => {
      if (event.payload === "open-settings") changeMode("settings");
      if (event.payload === "open-chat") changeMode("expanded");
      if (event.payload === "compact") changeMode("compact");
    }).then((dispose) => {
      unlisten = dispose;
    });
    return () => unlisten?.();
  }, [changeMode]);

  useEffect(() => {
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (overlayMode === "compact") void hideOverlay();
      else changeMode("compact");
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [changeMode, overlayMode]);

  if (overlayMode === "settings")
    return <SettingsShell onBack={() => changeMode("expanded")} />;
  if (overlayMode === "expanded")
    return (
      <Conversation
        state={assistantState}
        definition={definition}
        onCompact={() => changeMode("compact")}
        onSettings={() => changeMode("settings")}
        onStateChange={transitionTo}
        onPreviewState={previewState}
        coreStatus={coreStatus}
      />
    );
  return (
    <CompactOverlay
      state={assistantState}
      definition={definition}
      onExpand={() => changeMode("expanded")}
      onSettings={() => changeMode("settings")}
      onClose={() => void hideOverlay()}
    />
  );
}
