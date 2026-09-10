import {
  ArrowUp,
  ChevronDown,
  Mic,
  MoreHorizontal,
  Square,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import type { ModelStatusChanged } from "@moj-asystent/protocol";
import {
  assistantStates,
  AssistantState,
  AssistantStateDefinition,
} from "../domain/assistant";
import type { CoreConnectionStatus } from "../lib/coreClient";
import type { ConversationMessage } from "../hooks/useAssistant";
import { StatusOrb } from "./StatusOrb";
import { Waveform } from "./Waveform";

interface ConversationProps {
  state: AssistantState;
  definition: AssistantStateDefinition;
  onCompact: () => void;
  onSettings: () => void;
  onPreviewState: (state: AssistantState) => void;
  coreStatus: CoreConnectionStatus;
  stateSource: "core" | "simulation";
  messages: ConversationMessage[];
  onToggleListening: () => void;
  onSendMessage: (text: string) => Promise<boolean>;
  modelStatus?: ModelStatusChanged["payload"];
}

export function Conversation({
  state,
  definition,
  onCompact,
  onSettings,
  onPreviewState,
  coreStatus,
  stateSource,
  messages,
  onToggleListening,
  onSendMessage,
  modelStatus,
}: ConversationProps) {
  const [prompt, setPrompt] = useState("");
  const isWorking = ["thinking", "transcribing", "speaking"].includes(state);
  const isListening = ["listening", "wake_detected", "follow_up"].includes(
    state,
  );
  const canSend =
    coreStatus === "connected" && prompt.trim().length > 0 && !isWorking;
  const modelLabel =
    modelStatus?.status === "ready"
      ? `${modelStatus.model} · lokalnie`
      : modelStatus?.status === "loading"
        ? `${modelStatus.model} · odpowiada…`
        : modelStatus?.status === "missing"
          ? `Brak modelu ${modelStatus.model}`
          : modelStatus?.status === "error"
            ? "Model wymaga ponowienia"
            : "Ollama niedostępna";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSend) return;
    const message = prompt.trim();
    setPrompt("");
    void onSendMessage(message).then((sent) => {
      if (!sent) setPrompt(message);
    });
  };

  return (
    <main
      className="conversation overlay-surface"
      aria-label="Rozmowa z asystentem"
    >
      <header
        className="conversation__header drag-region"
        data-tauri-drag-region
      >
        <button
          className="brand-button"
          onClick={onCompact}
          aria-label="Zwiń do kompaktowej nakładki"
          data-tauri-drag-region="false"
        >
          <span className="brand-mark">M</span>
          <span>
            <strong>Mój Asystent</strong>
            <small>lokalnie na tym urządzeniu</small>
          </span>
        </button>
        <div
          className="conversation__header-actions"
          data-tauri-drag-region="false"
        >
          <span className="health-chip" aria-label={`Rdzeń: ${coreStatus}`}>
            <StatusOrb
              definition={
                coreStatus === "disconnected"
                  ? { ...definition, tone: "danger" }
                  : definition
              }
            />{" "}
            {coreStatus === "connected"
              ? "Rdzeń połączony"
              : coreStatus === "connecting"
                ? "Łączenie z rdzeniem"
                : "Rdzeń niedostępny"}
          </span>
          <button
            className="icon-button"
            onClick={onSettings}
            aria-label="Ustawienia"
          >
            <MoreHorizontal size={18} />
          </button>
        </div>
      </header>

      <section className="conversation__stream" aria-live="polite">
        <div className="context-strip">
          <span>Ten komputer</span>
          <span>{modelLabel}</span>
        </div>
        {messages.length === 0 ? (
          <article className="message message--assistant">
            <p className="message__label">Mój Asystent</p>
            <p>
              Powiedz lub napisz coś po polsku. Odpowiedź przygotuje lokalny
              model, a dane pozostaną na tym komputerze.
            </p>
          </article>
        ) : (
          messages.map((message) => (
            <article
              key={message.id}
              className={`message message--${message.role}`}
            >
              <p className="message__label">
                {message.role === "user" ? "Ty" : "Mój Asystent"}
              </p>
              <p>{message.text || (message.streaming ? "…" : "")}</p>
            </article>
          ))
        )}
        {isWorking && (
          <article className="status-message">
            <Waveform active={state === "speaking"} />
            <div>
              <strong>{definition.label}</strong>
              <span>{definition.detail}</span>
            </div>
          </article>
        )}
      </section>

      <form className="composer" onSubmit={submit}>
        <label className="sr-only" htmlFor="prompt">
          Zapytaj asystenta
        </label>
        <input
          id="prompt"
          placeholder="Zapytaj…"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          disabled={coreStatus !== "connected"}
          maxLength={8_192}
          aria-describedby="composer-help"
        />
        <span id="composer-help" className="sr-only">
          {modelStatus?.status === "missing"
            ? `Zainstaluj model poleceniem ollama pull ${modelStatus.model}.`
            : modelStatus?.detail || "Rozmowa jest przetwarzana lokalnie."}
        </span>
        <button
          className={`mic-button ${isListening ? "mic-button--active" : ""}`}
          onClick={onToggleListening}
          disabled={coreStatus !== "connected"}
          aria-label={
            isListening ? "Zatrzymaj nasłuchiwanie" : "Rozpocznij nasłuchiwanie"
          }
        >
          {isListening ? (
            <Square size={15} fill="currentColor" />
          ) : (
            <Mic size={18} />
          )}
        </button>
        <button
          className="send-button"
          disabled={!canSend}
          aria-label="Wyślij wiadomość"
        >
          <ArrowUp size={18} />
        </button>
      </form>
      <label className="state-switcher">
        <ChevronDown size={15} />{" "}
        {stateSource === "core" ? "Stan rdzenia" : "Stan demonstracyjny"}:{" "}
        {definition.label}
        <select
          value={state}
          onChange={(event) =>
            onPreviewState(event.target.value as AssistantState)
          }
          aria-label="Podgląd stanu asystenta"
          disabled={coreStatus === "connected"}
        >
          {assistantStates.map((assistantState) => (
            <option key={assistantState} value={assistantState}>
              {assistantState.replace("_", " ")}
            </option>
          ))}
        </select>
      </label>
    </main>
  );
}
