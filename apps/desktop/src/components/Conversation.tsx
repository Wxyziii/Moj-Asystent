import {
  ArrowUp,
  ChevronDown,
  Mic,
  MoreHorizontal,
  Square,
} from "lucide-react";
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
}: ConversationProps) {
  const isWorking = ["thinking", "transcribing", "speaking"].includes(state);
  const isListening = ["listening", "wake_detected", "follow_up"].includes(
    state,
  );

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
          <span>Brak aktywnego kontekstu</span>
        </div>
        {messages.length === 0 ? (
          <article className="message message--assistant">
            <p className="message__label">Mój Asystent</p>
            <p>
              Powiedz krótkie zdanie po polsku. Na tym etapie odpowiem
              deterministycznym komunikatem bez modelu AI.
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
              <p>{message.text}</p>
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

      <footer className="composer">
        <label className="sr-only" htmlFor="prompt">
          Zapytaj asystenta
        </label>
        <input
          id="prompt"
          placeholder="Zapytaj…"
          disabled
          aria-describedby="composer-help"
        />
        <span id="composer-help" className="sr-only">
          Pole tekstowe pozostaje wyłączone do etapu lokalnego czatu.
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
        <button className="send-button" disabled aria-label="Wyślij wiadomość">
          <ArrowUp size={18} />
        </button>
      </footer>
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
