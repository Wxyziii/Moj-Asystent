import {
  ArrowLeft,
  Check,
  ChevronRight,
  Keyboard,
  MonitorUp,
  ShieldCheck,
  SlidersHorizontal,
  Volume2,
} from "lucide-react";

interface SettingsShellProps {
  onBack: () => void;
  onWakeSettings: () => void;
  assistantName?: string;
}

interface SettingsSection {
  icon: typeof SlidersHorizontal;
  title: string;
  detail: string;
  value: string;
  action?: () => void;
}

const baseSections: SettingsSection[] = [
  {
    icon: MonitorUp,
    title: "Prywatność",
    detail: "Bez dostępu do ekranu w tym etapie",
    value: "Chronione",
  },
  {
    icon: ShieldCheck,
    title: "Uprawnienia",
    detail: "Brak narzędzi do zatwierdzenia",
    value: "Brak",
  },
];

export function SettingsShell({
  onBack,
  onWakeSettings,
  assistantName,
}: SettingsShellProps) {
  const sections: SettingsSection[] = [
    {
      icon: SlidersHorizontal,
      title: "Asystent",
      detail: assistantName
        ? `Imię aktywujące: ${assistantName}`
        : "Skonfiguruj imię aktywujące",
      value: assistantName ?? "Ustaw",
      action: onWakeSettings,
    },
    {
      icon: Volume2,
      title: "Głos i mikrofon",
      detail: "Czułość, kalibracja, zmiana mikrofonu i ponowny trening",
      value: "Zmień",
      action: onWakeSettings,
    },
    ...baseSections,
  ];
  return (
    <main className="settings overlay-surface" aria-label="Ustawienia">
      <header className="settings__header drag-region" data-tauri-drag-region>
        <button
          className="back-button"
          onClick={onBack}
          data-tauri-drag-region="false"
        >
          <ArrowLeft size={19} /> Wróć
        </button>
        <span>Ustawienia</span>
        <span className="header-spacer" />
      </header>
      <div className="settings__content">
        <section className="settings-intro">
          <p className="eyebrow">MÓJ ASYSTENT</p>
          <h1>Spokojnie. Lokalnie. Po Twojemu.</h1>
          <p>
            Imię, model aktywacji i ustawienia głosu pozostają lokalnie na tym
            komputerze. Surowe próbki są domyślnie usuwane po treningu.
          </p>
        </section>
        <section className="settings-group" aria-label="Obszary ustawień">
          {sections.map(({ icon: Icon, title, detail, value, action }) => (
            <button className="settings-row" key={title} onClick={action}>
              <span className="settings-row__icon">
                <Icon size={19} />
              </span>
              <span className="settings-row__copy">
                <strong>{title}</strong>
                <small>{detail}</small>
              </span>
              <span className="settings-row__value">
                {value === "Gotowe" ? <Check size={15} /> : null}
                {value}
              </span>
              <ChevronRight size={18} className="settings-row__chevron" />
            </button>
          ))}
        </section>
        <section className="shortcut-note">
          <Keyboard size={18} />
          <div>
            <strong>Skrót awaryjny</strong>
            <span>
              <kbd>Ctrl</kbd>
              <kbd>Shift</kbd>
              <kbd>Spacja</kbd> otwiera lub ukrywa nakładkę.
            </span>
          </div>
        </section>
      </div>
    </main>
  );
}
