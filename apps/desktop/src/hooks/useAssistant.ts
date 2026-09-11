import { useCallback, useEffect, useState } from "react";
import { type AssistantState, type OverlayMode } from "../domain/assistant";
import {
  connectToCore,
  sendAudioCommand,
  sendChatMessage,
  type CoreConnectionStatus,
  type CoreContentEvent,
} from "../lib/coreClient";
import type {
  ModelStatusChanged,
  ToolConfirmationRequested,
} from "@moj-asystent/protocol";
import {
  getCoreSessionCredential,
  resolveToolConfirmation,
  type ToolConfirmationDecision,
} from "../lib/desktop";

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
  const [pendingConfirmation, setPendingConfirmation] =
    useState<ToolConfirmationRequested["payload"]>();
  const [confirmationBusy, setConfirmationBusy] = useState(false);
  const [confirmationError, setConfirmationError] = useState<string>();
  const [toolActivity, setToolActivity] = useState<string>();
  const [contextChip, setContextChip] = useState<string>();
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
      if (event.type === "tool.execution.status") {
        const contextActivity =
          event.payload.tool_name === "read_ui_tree"
            ? "Odczytuję interfejs…"
            : event.payload.tool_name === "get_active_window"
              ? "Analizuję aktywne okno…"
              : undefined;
        setToolActivity(
          contextActivity ??
            (event.payload.status === "executing"
              ? `Wykonuję: ${event.payload.tool_name}`
              : event.payload.status === "confirmation_required"
                ? "Czekam na Twoją zgodę"
                : `Sprawdzam: ${event.payload.tool_name}`),
        );
        return;
      }
      if (event.type === "tool.confirmation.requested") {
        setPendingConfirmation(event.payload);
        setConfirmationBusy(false);
        setConfirmationError(undefined);
        return;
      }
      if (event.type === "tool.confirmation.resolved") {
        setPendingConfirmation((current) =>
          current?.confirmation_id === event.payload.confirmation_id
            ? undefined
            : current,
        );
        setConfirmationBusy(false);
        setConfirmationError(undefined);
        return;
      }
      if (event.type === "tool.result") {
        setToolActivity(event.payload.message);
        if (
          event.payload.status === "success" &&
          ["get_active_window", "read_ui_tree"].includes(
            event.payload.tool_name,
          )
        ) {
          setContextChip("Aktywne okno · kontekst gotowy");
        }
        setPendingConfirmation((current) =>
          current?.call_id === event.payload.call_id ? undefined : current,
        );
        setConfirmationBusy(false);
        return;
      }
      if (event.type === "assistant.response.started") {
        setPendingConfirmation(undefined);
        setConfirmationError(undefined);
        setToolActivity(undefined);
        setContextChip(undefined);
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
          (status) => {
            setCoreStatus(status);
            if (status !== "connected") {
              setPendingConfirmation(undefined);
              setConfirmationBusy(false);
              setContextChip(undefined);
            }
          },
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

  const decideConfirmation = useCallback(
    async (decision: ToolConfirmationDecision) => {
      if (!pendingConfirmation || confirmationBusy) return false;
      setConfirmationBusy(true);
      setConfirmationError(undefined);
      try {
        await resolveToolConfirmation(pendingConfirmation, decision);
        return true;
      } catch (error) {
        setConfirmationBusy(false);
        setConfirmationError(
          error instanceof Error
            ? error.message
            : "Nie udało się przekazać decyzji.",
        );
        return false;
      }
    },
    [confirmationBusy, pendingConfirmation],
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
    pendingConfirmation,
    confirmationBusy,
    confirmationError,
    toolActivity,
    contextChip,
    removeContext: () => setContextChip(undefined),
    decideConfirmation,
  };
}
