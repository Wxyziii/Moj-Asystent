import {
  ArrowLeft,
  Check,
  ChevronRight,
  Database,
  Eye,
  Keyboard,
  MonitorUp,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  Volume2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { CoreConnectionStatus } from "../lib/coreClient";
import {
  clearHistory,
  clearMemories,
  deleteAlias,
  deleteMemory,
  getMemorySettings,
  listAliases,
  listMemories,
  setHistoryRetention,
  type AliasRecord,
  type MemoryRecord,
} from "../lib/memoryClient";
import {
  cancelWatcher,
  deleteWatcher,
  listWatchers,
  pauseWatcher,
  resumeWatcher,
  type WatcherRecord,
} from "../lib/watcherClient";

interface SettingsShellProps {
  onBack: () => void;
  onWakeSettings: () => void;
  assistantName?: string;
  credential?: string;
  coreStatus: CoreConnectionStatus;
}

interface SettingsSection {
  icon: typeof SlidersHorizontal;
  title: string;
  detail: string;
  value: string;
  action?: () => void;
}

const watcherStatusLabels: Record<WatcherRecord["status"], string> = {
  active: "aktywna",
  paused: "wstrzymana",
  completed: "zakończona",
  failed: "błąd",
  cancelled: "anulowana",
  expired: "wygasła",
};

const watcherTypeLabels: Record<WatcherRecord["watcher_type"], string> = {
  window: "okno",
  process: "proces",
  file: "plik",
  resource: "zasób",
  build: "build",
  download: "pobieranie",
};

function watcherTargetLabel(watcher: WatcherRecord): string {
  const target = watcher.target;
  if (typeof target.path === "string") return target.path;
  if (typeof target.process_name === "string") return target.process_name;
  if (typeof target.pid === "number") return `PID ${target.pid}`;
  if (typeof target.title === "string") return target.title;
  return "Cel lokalny";
}

function watcherConditionLabel(watcher: WatcherRecord): string {
  const condition = watcher.condition;
  if (typeof condition.metric === "string") {
    const threshold =
      typeof condition.threshold === "number"
        ? ` · próg ${condition.threshold}`
        : "";
    return `${condition.metric}${threshold}`;
  }
  if (typeof condition.kind === "string") return condition.kind;
  return "warunek strukturalny";
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
  credential,
  coreStatus,
}: SettingsShellProps) {
  const [historyRetention, setHistoryRetentionState] = useState(false);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [aliases, setAliases] = useState<AliasRecord[]>([]);
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [memoryError, setMemoryError] = useState<string>();
  const [watchers, setWatchers] = useState<WatcherRecord[]>([]);
  const [watcherBusy, setWatcherBusy] = useState(false);
  const [watcherError, setWatcherError] = useState<string>();
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

  const refreshMemory = useCallback(async () => {
    if (!credential || coreStatus !== "connected") return;
    setMemoryBusy(true);
    setMemoryError(undefined);
    try {
      const [settings, records, aliasRecords] = await Promise.all([
        getMemorySettings(credential),
        listMemories(credential),
        listAliases(credential),
      ]);
      setHistoryRetentionState(settings.history_retention);
      setMemories(records);
      setAliases(aliasRecords);
      setWatchers(await listWatchers(credential));
    } catch (error) {
      setMemoryError(
        error instanceof Error
          ? error.message
          : "Nie udało się odczytać pamięci.",
      );
    } finally {
      setMemoryBusy(false);
    }
  }, [coreStatus, credential]);

  const changeWatcher = async (
    watcher: WatcherRecord,
    action: "pause" | "resume" | "cancel" | "delete",
  ) => {
    if (!credential || watcherBusy) return;
    setWatcherBusy(true);
    setWatcherError(undefined);
    try {
      if (action === "delete") {
        if (await deleteWatcher(watcher.watcher_id, credential))
          setWatchers((current) =>
            current.filter((item) => item.watcher_id !== watcher.watcher_id),
          );
      } else {
        const updated =
          action === "pause"
            ? await pauseWatcher(watcher.watcher_id, credential)
            : action === "resume"
              ? await resumeWatcher(watcher.watcher_id, credential)
              : await cancelWatcher(watcher.watcher_id, credential);
        setWatchers((current) =>
          current.map((item) =>
            item.watcher_id === updated.watcher_id ? updated : item,
          ),
        );
      }
    } catch (error) {
      setWatcherError(
        error instanceof Error
          ? error.message
          : "Nie udało się zmienić obserwacji.",
      );
    } finally {
      setWatcherBusy(false);
    }
  };

  useEffect(() => {
    void refreshMemory();
  }, [refreshMemory]);

  const toggleHistory = async () => {
    if (!credential || memoryBusy) return;
    const next = !historyRetention;
    setHistoryRetentionState(next);
    setMemoryBusy(true);
    setMemoryError(undefined);
    try {
      await setHistoryRetention(credential, next);
    } catch (error) {
      setHistoryRetentionState(!next);
      setMemoryError(
        error instanceof Error
          ? error.message
          : "Nie udało się zmienić ustawienia historii.",
      );
    } finally {
      setMemoryBusy(false);
    }
  };

  const removeMemory = async (memoryId: string) => {
    if (!credential || memoryBusy) return;
    setMemoryBusy(true);
    try {
      if (await deleteMemory(credential, memoryId))
        setMemories((current) =>
          current.filter((item) => item.memory_id !== memoryId),
        );
    } catch (error) {
      setMemoryError(
        error instanceof Error ? error.message : "Nie udało się usunąć wpisu.",
      );
    } finally {
      setMemoryBusy(false);
    }
  };

  const removeAlias = async (aliasId: string) => {
    if (!credential || memoryBusy) return;
    setMemoryBusy(true);
    try {
      if (await deleteAlias(credential, aliasId))
        setAliases((current) =>
          current.filter((item) => item.alias_id !== aliasId),
        );
    } catch (error) {
      setMemoryError(
        error instanceof Error ? error.message : "Nie udało się usunąć aliasu.",
      );
    } finally {
      setMemoryBusy(false);
    }
  };

  const clearHistoryClick = async () => {
    if (
      !credential ||
      memoryBusy ||
      !window.confirm("Usunąć zapisaną historię rozmów?")
    )
      return;
    setMemoryBusy(true);
    try {
      await clearHistory(credential);
    } catch (error) {
      setMemoryError(
        error instanceof Error
          ? error.message
          : "Nie udało się wyczyścić historii.",
      );
    } finally {
      setMemoryBusy(false);
    }
  };

  const clearMemoryClick = async () => {
    if (
      !credential ||
      memoryBusy ||
      !window.confirm("Usunąć wszystkie pamięci i aliasy?")
    )
      return;
    setMemoryBusy(true);
    try {
      await clearMemories(credential);
      setMemories([]);
      setAliases([]);
    } catch (error) {
      setMemoryError(
        error instanceof Error
          ? error.message
          : "Nie udało się wyczyścić pamięci.",
      );
    } finally {
      setMemoryBusy(false);
    }
  };
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
        <section className="memory-settings" aria-label="Pamięć lokalna">
          <div className="memory-settings__heading">
            <span className="settings-row__icon">
              <Database size={19} />
            </span>
            <div>
              <strong>Pamięć lokalna</strong>
              <small>
                Tylko jawnie zapisane informacje, na tym komputerze.
              </small>
            </div>
          </div>
          {coreStatus !== "connected" ? (
            <p className="memory-settings__muted">
              Połącz rdzeń, aby zarządzać pamięcią.
            </p>
          ) : (
            <>
              <label className="memory-settings__toggle">
                <span>
                  <strong>Historia rozmów</strong>
                  <small>Zapisuje ukończone wiadomości, nigdy nagrania.</small>
                </span>
                <input
                  type="checkbox"
                  checked={historyRetention}
                  onChange={() => void toggleHistory()}
                  disabled={memoryBusy}
                />
              </label>
              {memories.length > 0 ? (
                <div className="memory-list">
                  {memories.map((memory) => (
                    <div
                      className="memory-list__item"
                      key={`${memory.category}:${memory.memory_id}:${memory.key}`}
                    >
                      <span>
                        <strong>{memory.key}</strong>
                        <small>{memory.value}</small>
                      </span>
                      {memory.category === "memory" ? (
                        <button
                          type="button"
                          className="icon-button"
                          aria-label={`Usuń ${memory.key}`}
                          onClick={() => void removeMemory(memory.memory_id)}
                          disabled={memoryBusy}
                        >
                          <Trash2 size={15} />
                        </button>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="memory-settings__muted">
                  Nie zapisano jeszcze żadnych informacji.
                </p>
              )}
              {aliases.length > 0 ? (
                <div className="memory-list" aria-label="Zapisane aliasy">
                  {aliases.map((alias) => (
                    <div className="memory-list__item" key={alias.alias_id}>
                      <span>
                        <strong>{alias.alias}</strong>
                        <small>{alias.target}</small>
                      </span>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={`Usuń alias ${alias.alias}`}
                        onClick={() => void removeAlias(alias.alias_id)}
                        disabled={memoryBusy}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
              <div className="memory-settings__actions">
                <button
                  type="button"
                  onClick={() => void clearHistoryClick()}
                  disabled={memoryBusy}
                >
                  Wyczyść historię
                </button>
                <button
                  type="button"
                  onClick={() => void clearMemoryClick()}
                  disabled={memoryBusy}
                >
                  Wyczyść pamięć i aliasy
                </button>
              </div>
              {memoryError ? (
                <p className="memory-settings__error">{memoryError}</p>
              ) : null}
            </>
          )}
        </section>
        <section className="memory-settings" aria-label="Obserwacje">
          <div className="memory-settings__heading">
            <span className="settings-row__icon">
              <Eye size={19} />
            </span>
            <div>
              <strong>Obserwacje</strong>
              <small>
                Tylko obserwacje, o które poprosisz. Bez automatycznych działań.
              </small>
            </div>
          </div>
          {coreStatus !== "connected" ? (
            <p className="memory-settings__muted">
              Połącz rdzeń, aby zarządzać obserwacjami.
            </p>
          ) : watchers.length === 0 ? (
            <p className="memory-settings__muted">
              Nie masz aktywnych obserwacji.
            </p>
          ) : (
            <div className="memory-list">
              {watchers.map((watcher) => (
                <div className="memory-list__item" key={watcher.watcher_id}>
                  <span>
                    <strong>{watcher.name}</strong>
                    <small>
                      {watcherStatusLabels[watcher.status]} ·{" "}
                      {watcherTypeLabels[watcher.watcher_type]}
                    </small>
                    <small className="watcher-settings__target">
                      {watcherTargetLabel(watcher)} ·{" "}
                      {watcherConditionLabel(watcher)}
                    </small>
                  </span>
                  <span className="watcher-settings__actions">
                    {watcher.status === "active" ? (
                      <button
                        type="button"
                        className="icon-button"
                        onClick={() => void changeWatcher(watcher, "pause")}
                        disabled={watcherBusy}
                        aria-label="Wstrzymaj obserwację"
                      >
                        Ⅱ
                      </button>
                    ) : watcher.status === "paused" ? (
                      <button
                        type="button"
                        className="icon-button"
                        onClick={() => void changeWatcher(watcher, "resume")}
                        disabled={watcherBusy}
                        aria-label="Wznów obserwację"
                      >
                        ▶
                      </button>
                    ) : null}
                    {watcher.status === "active" ||
                    watcher.status === "paused" ? (
                      <button
                        type="button"
                        className="icon-button"
                        onClick={() => void changeWatcher(watcher, "cancel")}
                        disabled={watcherBusy}
                        aria-label="Zatrzymaj obserwację"
                      >
                        ×
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="icon-button"
                      onClick={() => void changeWatcher(watcher, "delete")}
                      disabled={watcherBusy}
                      aria-label="Usuń obserwację"
                    >
                      <Trash2 size={15} />
                    </button>
                  </span>
                </div>
              ))}
            </div>
          )}
          {watcherError && (
            <p className="memory-settings__error">{watcherError}</p>
          )}
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
