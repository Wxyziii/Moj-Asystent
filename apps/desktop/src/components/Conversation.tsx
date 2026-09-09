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
import { StatusOrb } from "./StatusOrb";
import { Waveform } from "./Waveform";

interface ConversationProps {
  state: AssistantState;
  definition: AssistantStateDefinition;
  onCompact: () => void;
  onSettings: () => void;
  onStateChange: (state: AssistantState) => void;
  onPreviewState: (state: AssistantState) => void;
}

export function Conversation({
  state,
  definition,
  onCompact,
  onSettings,
  onStateChange,
  onPreviewState,
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
          <span className="health-chip">
            <StatusOrb definition={definition} /> Symulacja
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
        <article className="message message--user">
          <p className="message__label">Ty</p>
          <p>Dlaczego ten program nie działa?</p>
        </article>
        <article className="message message--assistant">
          <p className="message__label">Mój Asystent</p>
          <p>
            W tej wersji pokazuję tylko interfejs. Gdy połączymy usługę w
            kolejnym etapie, tu pojawi się odpowiedź oparta na aktualnym
            kontekście.
          </p>
          <div className="suggestion-row">
            <button onClick={() => onStateChange("thinking")}>
              Pokaż przykład
            </button>
            <button onClick={() => onStateChange("error")}>
              Zasymuluj problem
            </button>
          </div>
        </article>
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
          W Milestone 1 pole jest elementem demonstracyjnym.
        </span>
        <button
          className={`mic-button ${isListening ? "mic-button--active" : ""}`}
          onClick={() => onStateChange(isListening ? "idle" : "listening")}
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
        <ChevronDown size={15} /> Stan demonstracyjny: {definition.label}
        <select
          value={state}
          onChange={(event) =>
            onPreviewState(event.target.value as AssistantState)
          }
          aria-label="Podgląd stanu asystenta"
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
