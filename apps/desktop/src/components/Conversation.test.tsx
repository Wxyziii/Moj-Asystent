import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { ToolConfirmationRequested } from "@moj-asystent/protocol";

import { assistantStateDefinitions } from "../domain/assistant";
import { Conversation } from "./Conversation";

const request: ToolConfirmationRequested["payload"] = {
  confirmation_id: "a".repeat(43),
  operation_id: crypto.randomUUID(),
  call_id: crypto.randomUUID(),
  tool_name: "delete_file",
  arguments_digest: "b".repeat(64),
  action: "Usunąć plik?",
  target: "C:\\Users\\Test\\notes.txt",
  details: [{ label: "Plik", value: "notes.txt" }],
  risk: "Tej czynności nie można cofnąć.",
  expires_at: "2026-09-11T08:00:00Z",
  persistent_allowed: false,
};

function renderConfirmation(persistentAllowed: boolean) {
  return renderToStaticMarkup(
    <Conversation
      state="thinking"
      definition={assistantStateDefinitions.thinking}
      onCompact={vi.fn()}
      onSettings={vi.fn()}
      onPreviewState={vi.fn()}
      coreStatus="connected"
      stateSource="core"
      messages={[]}
      onToggleListening={vi.fn()}
      onSendMessage={async () => true}
      pendingConfirmation={{
        ...request,
        persistent_allowed: persistentAllowed,
      }}
      confirmationBusy={false}
      onConfirmationDecision={vi.fn()}
    />,
  );
}

it("renders sensitive confirmation as a separate exact action card", () => {
  const html = renderConfirmation(false);
  expect(html).toContain("Wymaga Twojej zgody");
  expect(html).toContain("Usunąć plik?");
  expect(html).toContain("notes.txt");
  expect(html).toContain("Zezwól raz");
  expect(html).toContain("Anuluj");
  expect(html).not.toContain("Zawsze zezwalaj");
});

it("shows persistent approval only when the core explicitly permits it", () => {
  expect(renderConfirmation(true)).toContain("Zawsze zezwalaj");
});

it("shows a removable context chip without exposing the UI tree", () => {
  const html = renderToStaticMarkup(
    <Conversation
      state="thinking"
      definition={assistantStateDefinitions.thinking}
      onCompact={vi.fn()}
      onSettings={vi.fn()}
      onPreviewState={vi.fn()}
      coreStatus="connected"
      stateSource="core"
      messages={[]}
      onToggleListening={vi.fn()}
      onSendMessage={async () => true}
      confirmationBusy={false}
      contextChip="Aktywne okno · kontekst gotowy"
      onRemoveContext={vi.fn()}
      onConfirmationDecision={vi.fn()}
    />,
  );

  expect(html).toContain("Aktywne okno · kontekst gotowy");
  expect(html).toContain('aria-label="Usuń kontekst aktywnego okna"');
});
