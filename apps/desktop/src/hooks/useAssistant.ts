import { useCallback, useEffect, useState } from "react";
import { type AssistantState, type OverlayMode } from "../domain/assistant";
import {
  connectToCore,
  sendAudioCommand,
  type CoreConnectionStatus,
  type CoreContentEvent,
} from "../lib/coreClient";
import { getCoreSessionCredential } from "../lib/desktop";

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
}

export function useAssistantUi() {
  const [assistantState, setAssistantState] = useState<AssistantState>("idle");
  const [coreStatus, setCoreStatus] =
    useState<CoreConnectionStatus>("connecting");
  const [stateSource, setStateSource] = useState<"core" | "simulation">(
    "simulation",
  );
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [credential, setCredential] = useState<string>();
  const [overlayMode, setOverlayMode] = useState<OverlayMode>(() => {
    const savedMode = window.localStorage.getItem("moj-asystent.overlay-mode");
    return savedMode === "expanded" || savedMode === "settings"
      ? savedMode
      : "compact";
  });

  useEffect(() => {
    window.localStorage.setItem("moj-asystent.overlay-mode", overlayMode);
  }, [overlayMode]);

  useEffect(() => {
    let stop: (() => void) | undefined;
    let disposed = false;
    const content = (event: CoreContentEvent) => {
      const role: ConversationMessage["role"] =
        event.type === "audio.transcript.final" ? "user" : "assistant";
      setMessages((current) =>
        [
          ...current.filter(
            (message) => message.id !== event.payload.operation_id + role,
          ),
          {
            id: event.payload.operation_id + role,
            role,
            text: event.payload.text,
          },
        ].slice(-50),
      );
    };
    void getCoreSessionCredential()
      .then((token) => {
        if (disposed) return;
        setCredential(token);
        stop = connectToCore(
          setCoreStatus,
          (state) => {
            setStateSource("core");
            setAssistantState(state);
          },
          token,
          content,
        );
      })
      .catch(() => setCoreStatus("disconnected"));
    return () => {
      disposed = true;
      stop?.();
    };
  }, []);

  const previewState = useCallback(
    (state: AssistantState) => {
      if (coreStatus === "connected") return;
      setStateSource("simulation");
      setAssistantState(state);
    },
    [coreStatus],
  );

  const toggleListening = useCallback(async () => {
    if (!credential || coreStatus !== "connected") return;
    try {
      await sendAudioCommand(
        [
          "listening",
          "wake_detected",
          "follow_up",
          "transcribing",
          "thinking",
          "speaking",
        ].includes(assistantState)
          ? "cancel"
          : "listen",
        credential,
      );
    } catch {
      setCoreStatus("disconnected");
    }
  }, [assistantState, coreStatus, credential]);

  return {
    assistantState,
    coreStatus,
    overlayMode,
    setOverlayMode,
    previewState,
    stateSource,
    messages,
    toggleListening,
  };
}
