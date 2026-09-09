import { MessageCircleMore, Settings2, X } from "lucide-react";
import type {
  AssistantState,
  AssistantStateDefinition,
} from "../domain/assistant";
import { StatusOrb } from "./StatusOrb";
import { Waveform } from "./Waveform";

interface CompactOverlayProps {
  state: AssistantState;
  definition: AssistantStateDefinition;
  onExpand: () => void;
  onSettings: () => void;
  onClose: () => void;
}

export function CompactOverlay({
  state,
  definition,
  onExpand,
  onSettings,
  onClose,
}: CompactOverlayProps) {
  const listening =
    state === "listening" || state === "wake_detected" || state === "follow_up";

  return (
    <section
      className="compact-overlay overlay-surface"
      aria-label="Status asystenta"
    >
      <div
        className="drag-region compact-overlay__header"
        data-tauri-drag-region
      >
        <div className="status-line">
          <StatusOrb definition={definition} />
          <span>{definition.label}</span>
        </div>
        <div className="window-actions" data-tauri-drag-region="false">
          <button
            className="icon-button"
            onClick={onSettings}
            aria-label="Otwórz ustawienia"
          >
            <Settings2 size={16} />
          </button>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Ukryj nakładkę"
          >
            <X size={17} />
          </button>
        </div>
      </div>
      <div className="compact-overlay__body">
        <div>
          <p>{definition.detail}</p>
          <span className="state-caption">
            Tryb demonstracyjny · Ctrl + Shift + Spacja
          </span>
        </div>
        <Waveform active={listening} />
      </div>
      <button className="expand-button" onClick={onExpand}>
        <MessageCircleMore size={17} />
        Otwórz rozmowę
      </button>
    </section>
  );
}
