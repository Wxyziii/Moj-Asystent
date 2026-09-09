# Overlay UI Specification

## Product intent

The overlay is the primary visible interface. Voice can wake the assistant without a hotkey, but visual context, confirmations, progress and richer responses appear in the overlay.

Style target: minimal, modern desktop UI with restrained Apple/iOS-inspired clarity while still feeling native on Windows.

## Window modes

### 1. Hidden/idle

- no large persistent window;
- optional tiny tray/orb indicator;
- wake-word engine may still be active.

### 2. Listening bubble

Compact always-on-top panel:

```text
╭──────────────────────────────╮
│ ●  Słucham...                │
│                              │
│ ▁▂▄▆█▆▄▂                     │
╰──────────────────────────────╯
```

Requirements:

- microphone waveform/activity;
- chosen assistant name or neutral branding;
- clear active-listening indicator;
- cancel button/escape behavior;
- should not steal focus unnecessarily.

### 3. Thinking/transcribing bubble

Show state, not hidden reasoning.

Examples:

- `Przepisuję...`
- `Sprawdzam aktywne okno...`
- `Analizuję ekran...`
- `Sprawdzam użycie GPU...`

Never expose hidden chain-of-thought.

### 4. Expanded conversation

```text
┌─────────────────────────────────────────────┐
│ KAIRO                                  ●    │
├─────────────────────────────────────────────┤
│                                             │
│ Ty                                          │
│ Dlaczego ten program nie działa?            │
│                                             │
│ Kairo                                       │
│ Port 3000 jest już używany przez inny       │
│ proces.                                     │
│                                             │
│ [ Sprawdź proces ] [ Napraw ] [ Szczegóły ] │
│                                             │
├─────────────────────────────────────────────┤
│ Zapytaj...                              🎤  │
└─────────────────────────────────────────────┘
```

Requirements:

- streamed responses;
- scrollable conversation;
- code/markdown rendering;
- tool cards;
- context indicators;
- text input and microphone control;
- expandable detail sections;
- stop/cancel generation.

## Suggested actions

Models may propose actions, but UI renders only actions mapped to registered tools.

Examples:

- `Wyjaśnij`
- `Pokaż szczegóły`
- `Sprawdź proces`
- `Otwórz folder`
- `Napraw`
- `Ignoruj`

Never render a destructive action as a one-click ordinary suggestion without the required confirmation policy.

## Confirmation card

```text
┌────────────────────────────────────┐
│ Wymagane potwierdzenie             │
│                                    │
│ Zamknąć proces node.exe?           │
│ PID: 19234                         │
│                                    │
│ [ Zezwól ] [ Zawsze zezwalaj ]     │
│ [ Anuluj ]                         │
└────────────────────────────────────┘
```

`Zawsze zezwalaj` appears only for tools/categories where persistent permission is acceptable.

## Screen inspection UX

When vision is used, make it visible:

```text
👁 Analizuję aktywne okno
```

If a screenshot/region is captured, show a small preview or context chip and allow the user to remove it before re-running a request.

## Region selection

Fallback hotkey/menu command:

1. dim desktop;
2. crosshair/drag rectangle;
3. capture region;
4. open overlay with preview;
5. user asks `Co to znaczy?` or enters text.

## Proactive suggestion bubble

Small, non-disruptive:

```text
╭────────────────────────────────────╮
│ Build zakończył się błędem.        │
│ Mogę sprawdzić log.                │
│                                    │
│ [ Sprawdź ] [ Później ]            │
╰────────────────────────────────────╯
```

Rules:

- no spam;
- configurable proactive level;
- suppress/minimize during games/full-screen apps;
- watcher-triggered events should be traceable to what the user asked to watch.

## Settings areas

### Assistant

- display name;
- rename/retrain wake word;
- language (locked to Polish in V1);
- follow-up timeout;
- response verbosity.

### Voice

- microphone;
- wake listening toggle;
- sensitivity;
- recalibration;
- TTS voice;
- voice response toggle.

### AI

- Fast / Balanced / Deep;
- local-only toggle;
- model status;
- model download/manage screen later.

### Privacy

- excluded applications/windows;
- screen-awareness toggle;
- retain conversation history;
- retain training samples;
- clear local data.

### Permissions

- tool permissions;
- application allowlist;
- path allowlist;
- reset approvals.

### Watchers

- active watchers;
- pause/delete;
- notification style.

## Tray menu

Initial:

```text
Mój Asystent
✓ Nasłuchiwanie imienia
✓ Odpowiedzi głosowe
✓ Świadomość ekranu

Otwórz czat
Wycisz mikrofon
Ustawienia
Zakończ
```

## Accessibility

- full keyboard navigation;
- clear focus states;
- sufficient contrast;
- scalable text;
- no color-only meaning for listening/permission states;
- reduced motion option.

## Multi-monitor behavior

- default overlay appears on monitor containing the active window/cursor;
- remember user override;
- region selection can span the chosen monitor only initially;
- avoid moving unexpectedly when focus changes during an active conversation.

## Performance

Overlay animation must remain smooth even while a model is loaded.

Do not run heavy AI work in the UI process. Use the core service and stream only state/results to Tauri.
