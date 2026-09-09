export { assistantStates } from "@moj-asystent/protocol";
export type { AssistantState } from "@moj-asystent/protocol";
import type { AssistantState } from "@moj-asystent/protocol";
export type OverlayMode = "compact" | "expanded" | "settings";

export interface AssistantStateDefinition {
  label: string;
  detail: string;
  tone: "quiet" | "active" | "working" | "speaking" | "danger";
  compactMode?: boolean;
}

export const assistantStateDefinitions: Record<
  AssistantState,
  AssistantStateDefinition
> = {
  idle: { label: "Gotowy", detail: "Czekam na Ciebie", tone: "quiet" },
  wake_detected: {
    label: "Słyszę Cię",
    detail: "Rozpoznano sygnał aktywacji",
    tone: "active",
    compactMode: true,
  },
  listening: {
    label: "Słucham…",
    detail: "Powiedz, w czym mogę pomóc",
    tone: "active",
    compactMode: true,
  },
  transcribing: {
    label: "Przepisuję…",
    detail: "Układam Twoje pytanie",
    tone: "working",
    compactMode: true,
  },
  thinking: {
    label: "Przygotowuję odpowiedź…",
    detail: "To tylko symulacja interfejsu",
    tone: "working",
  },
  speaking: {
    label: "Odpowiadam…",
    detail: "Możesz przerwać w dowolnym momencie",
    tone: "speaking",
  },
  follow_up: {
    label: "Czekam na dopowiedzenie…",
    detail: "Nie musisz ponownie aktywować asystenta",
    tone: "active",
  },
  error: {
    label: "Nie mogę się połączyć",
    detail: "Usługa asystenta jest obecnie niedostępna",
    tone: "danger",
  },
};

export const canTransition = (
  from: AssistantState,
  to: AssistantState,
): boolean => {
  const transitions: Record<AssistantState, AssistantState[]> = {
    idle: ["wake_detected", "listening", "error"],
    wake_detected: ["listening", "idle", "error"],
    listening: ["transcribing", "idle", "error"],
    transcribing: ["thinking", "idle", "error"],
    thinking: ["speaking", "follow_up", "idle", "error"],
    speaking: ["follow_up", "idle", "error"],
    follow_up: ["listening", "idle", "error"],
    error: ["idle"],
  };

  return from === to || transitions[from].includes(to);
};
