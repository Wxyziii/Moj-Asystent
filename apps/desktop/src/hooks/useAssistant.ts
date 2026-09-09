import { useCallback, useEffect, useState } from "react";
import {
  canTransition,
  type AssistantState,
  type OverlayMode,
} from "../domain/assistant";
import { connectToCore, type CoreConnectionStatus } from "../lib/coreClient";

export function useAssistantUi() {
  const [assistantState, setAssistantState] = useState<AssistantState>("idle");
  const [coreStatus, setCoreStatus] =
    useState<CoreConnectionStatus>("connecting");
  const [overlayMode, setOverlayMode] = useState<OverlayMode>(() => {
    const savedMode = window.localStorage.getItem("moj-asystent.overlay-mode");
    return savedMode === "expanded" || savedMode === "settings"
      ? savedMode
      : "compact";
  });

  useEffect(() => {
    window.localStorage.setItem("moj-asystent.overlay-mode", overlayMode);
  }, [overlayMode]);

  useEffect(
    () =>
      connectToCore(setCoreStatus, (nextState) => {
        setAssistantState((currentState) =>
          canTransition(currentState, nextState) ? nextState : currentState,
        );
      }),
    [],
  );

  const transitionTo = useCallback((nextState: AssistantState) => {
    setAssistantState((currentState) =>
      canTransition(currentState, nextState) ? nextState : currentState,
    );
  }, []);

  return {
    assistantState,
    coreStatus,
    overlayMode,
    setOverlayMode,
    transitionTo,
    previewState: setAssistantState,
  };
}
