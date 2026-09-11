import { useCallback, useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { CompactOverlay } from "./components/CompactOverlay";
import { Conversation } from "./components/Conversation";
import { RegionSelector } from "./components/RegionSelector";
import { SettingsShell } from "./components/SettingsShell";
import { WakeOnboarding } from "./components/WakeOnboarding";
import {
  assistantStateDefinitions,
  type OverlayMode,
} from "./domain/assistant";
import { useAssistantUi } from "./hooks/useAssistant";
import {
  beginRegionSelection,
  hideOverlay,
  requestDesktopMode,
} from "./lib/desktop";
import { getOnboardingStatus } from "./lib/onboardingClient";

export default function App() {
  if (new URLSearchParams(window.location.search).has("region-selector"))
    return <RegionSelector />;
  return <AssistantApp />;
}

function AssistantApp() {
  const onboardingPreview =
    import.meta.env.DEV &&
    new URLSearchParams(window.location.search).has("onboarding-preview");
  const {
    assistantState,
    coreStatus,
    overlayMode,
    setOverlayMode,
    previewState,
    stateSource,
    messages,
    toggleListening,
    sendMessage,
    modelStatus,
    credential,
    pendingConfirmation,
    confirmationBusy,
    confirmationError,
    toolActivity,
    contextChip,
    visualContext,
    removeContext,
    decideConfirmation,
    notifications,
    dismissNotification,
    stopWatcher,
  } = useAssistantUi();
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [assistantName, setAssistantName] = useState<string>();
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

  useEffect(() => {
    if (!credential || coreStatus !== "connected") return;
    void getOnboardingStatus(credential).then((status) => {
      setAssistantName(status.active?.assistant_name);
      if (!status.completed) {
        setShowOnboarding(true);
        void requestDesktopMode("settings");
      }
    });
  }, [coreStatus, credential]);

  if (onboardingPreview)
    return (
      <WakeOnboarding
        credential={"P".repeat(43)}
        onComplete={() => undefined}
      />
    );

  if (showOnboarding && credential)
    return (
      <WakeOnboarding
        credential={credential}
        initialName={assistantName}
        onComplete={(nextName) => {
          setAssistantName(nextName);
          setShowOnboarding(false);
          changeMode("expanded");
        }}
        onClose={assistantName ? () => setShowOnboarding(false) : undefined}
      />
    );

  if (overlayMode === "settings")
    return (
      <SettingsShell
        assistantName={assistantName}
        credential={credential}
        coreStatus={coreStatus}
        onWakeSettings={() => {
          setShowOnboarding(true);
          void requestDesktopMode("settings");
        }}
        onBack={() => changeMode("expanded")}
      />
    );
  if (overlayMode === "expanded")
    return (
      <Conversation
        state={assistantState}
        definition={definition}
        onCompact={() => changeMode("compact")}
        onSettings={() => changeMode("settings")}
        onPreviewState={previewState}
        coreStatus={coreStatus}
        stateSource={stateSource}
        messages={messages}
        onToggleListening={() => void toggleListening()}
        onSendMessage={sendMessage}
        modelStatus={modelStatus}
        pendingConfirmation={pendingConfirmation}
        confirmationBusy={confirmationBusy}
        confirmationError={confirmationError}
        toolActivity={toolActivity}
        contextChip={contextChip}
        visualContext={visualContext}
        onSelectRegion={() => void beginRegionSelection()}
        onRemoveContext={removeContext}
        onConfirmationDecision={(decision) => void decideConfirmation(decision)}
        notifications={notifications}
        onDismissNotification={dismissNotification}
        onStopNotification={(notificationId, watcherId) =>
          void stopWatcher(notificationId, watcherId)
        }
      />
    );
  return (
    <CompactOverlay
      state={assistantState}
      definition={definition}
      onExpand={() => changeMode("expanded")}
      onSettings={() => changeMode("settings")}
      onClose={() => void hideOverlay()}
      stateSource={stateSource}
      coreStatus={coreStatus}
    />
  );
}
