import { useCallback, useEffect, useState } from "react";
import { type AssistantState, type OverlayMode } from "../domain/assistant";
import {
  connectToCore,
  sendAudioCommand,
  sendChatMessage,
  type CoreConnectionStatus,
  type CoreContentEvent,
} from "../lib/coreClient";
import type { ModelStatusChanged } from "@moj-asystent/protocol";
import { getCoreSessionCredential } from "../lib/desktop";

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
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
  const [modelStatus, setModelStatus] =
    useState<ModelStatusChanged["payload"]>();
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
      if (event.type === "model.status.changed") {
        setModelStatus(event.payload);
        return;
      }
      if (event.type === "assistant.response.started") {
        setMessages((current) =>
          [
            ...current.filter(
              (message) =>
                message.id !== event.payload.operation_id + "assistant",
            ),
            {
              id: event.payload.operation_id + "assistant",
              role: "assistant" as const,
              text: "",
              streaming: true,
            },
          ].slice(-50),
        );
        return;
      }
      if (event.type === "assistant.response.delta") {
        setMessages((current) =>
          current.map((message) =>
            message.id === event.payload.operation_id + "assistant"
              ? { ...message, text: message.text + event.payload.text }
              : message,
          ),
        );
        return;
      }
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
            streaming: false,
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

  const sendMessage = useCallback(
    async (text: string) => {
      if (!credential || coreStatus !== "connected") return false;
      const normalized = text.trim();
      if (!normalized) return false;
      setMessages((current) =>
        [
          ...current,
          {
            id: `local-${crypto.randomUUID()}`,
            role: "user" as const,
            text: normalized,
          },
        ].slice(-50),
      );
      try {
        await sendChatMessage(normalized, credential);
        return true;
      } catch {
        setCoreStatus("disconnected");
        return false;
      }
    },
    [coreStatus, credential],
  );

  return {
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
  };
}
