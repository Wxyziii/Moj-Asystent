import { useCallback, useEffect, useState } from "react";
import {
  canTransition,
  type AssistantState,
  type OverlayMode,
} from "../domain/assistant";

export function useAssistantUi() {
  const [assistantState, setAssistantState] = useState<AssistantState>("idle");
  const [overlayMode, setOverlayMode] = useState<OverlayMode>(() => {
    const savedMode = window.localStorage.getItem("moj-asystent.overlay-mode");
    return savedMode === "expanded" || savedMode === "settings"
      ? savedMode
      : "compact";
  });

  useEffect(() => {
    window.localStorage.setItem("moj-asystent.overlay-mode", overlayMode);
  }, [overlayMode]);

  const transitionTo = useCallback((nextState: AssistantState) => {
    setAssistantState((currentState) =>
      canTransition(currentState, nextState) ? nextState : currentState,
    );
  }, []);

  return {
    assistantState,
    overlayMode,
    setOverlayMode,
    transitionTo,
    previewState: setAssistantState,
  };
}
