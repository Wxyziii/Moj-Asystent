import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  CircleStop,
  Mic,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Volume2,
  X,
} from "lucide-react";
import type {
  OnboardingSession,
  RecordingStep,
  SampleQuality,
  TrainingJob,
  ValidationMetrics,
} from "@moj-asystent/protocol";
import {
  activateWakeModel,
  beginOnboarding,
  getOnboardingSession,
  cancelOnboarding,
  cancelTraining,
  getTraining,
  sendCalibration,
  sendSample,
  sendValidation,
  setWakeSensitivity,
  startTraining,
} from "../lib/onboardingClient";
import {
  capturePcm,
  listMicrophones,
  type MicrophoneChoice,
} from "../lib/pcmRecorder";

type Stage =
  | "welcome"
  | "name"
  | "assessment"
  | "microphone"
  | "calibration"
  | "recordings"
  | "training"
  | "validation"
  | "sensitivity"
  | "voice"
  | "ready";

interface WakeOnboardingProps {
  credential: string;
  initialName?: string;
  onComplete: (name: string) => void;
  onClose?: () => void;
}

const stageNumber: Record<Stage, number> = {
  welcome: 0,
  name: 1,
  assessment: 2,
  microphone: 3,
  calibration: 4,
  recordings: 5,
  training: 6,
  validation: 7,
  sensitivity: 8,
  voice: 9,
  ready: 10,
};

export function WakeOnboarding({
  credential,
  initialName = "",
  onComplete,
  onClose,
}: WakeOnboardingProps) {
  const [stage, setStage] = useState<Stage>(initialName ? "name" : "welcome");
  const [name, setName] = useState(initialName);
  const [session, setSession] = useState<OnboardingSession>();
  const [microphones, setMicrophones] = useState<MicrophoneChoice[]>([]);
  const [microphone, setMicrophone] = useState<MicrophoneChoice>();
  const [recordingIndex, setRecordingIndex] = useState(0);
  const [quality, setQuality] = useState<SampleQuality>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [job, setJob] = useState<TrainingJob>();
  const [metrics, setMetrics] = useState<ValidationMetrics>();
  const [positiveAttempts, setPositiveAttempts] = useState(0);
  const [negativeDone, setNegativeDone] = useState(false);
  const [sensitivity, setSensitivity] = useState(0.5);
  const step = session?.curriculum[recordingIndex];

  useEffect(() => {
    if (stage !== "training" || !job || job.status !== "running") return;
    const timer = window.setInterval(() => {
      void getTraining(credential, job.job_id)
        .then((next) => {
          setJob(next);
          if (next.status === "ready") setStage("validation");
          if (next.status === "failed")
            setError(next.error ?? "Trening nie powiódł się.");
        })
        .catch((reason: unknown) => setError(message(reason)));
    }, 700);
    return () => window.clearInterval(timer);
  }, [credential, job, stage]);

  const progress = useMemo(
    () => Math.round((stageNumber[stage] / 10) * 100),
    [stage],
  );

  async function createSession() {
    setBusy(true);
    setError(undefined);
    try {
      const created = await beginOnboarding(
        credential,
        name,
        microphone?.label ?? null,
        false,
      );
      setSession(created);
      setName(created.name.display_name);
      setStage("assessment");
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function discoverMicrophones() {
    setBusy(true);
    setError(undefined);
    try {
      const choices = await listMicrophones();
      setMicrophones(choices);
      setMicrophone(choices[0]);
      setStage("microphone");
    } catch {
      setError(
        "Nie udało się uzyskać dostępu do mikrofonu. Sprawdź uprawnienia Windows.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function calibrate() {
    if (!session) return;
    setBusy(true);
    setError(undefined);
    try {
      const result = await sendCalibration(
        credential,
        session.session_id,
        await capturePcm(3, microphone?.id),
      );
      if (!result.ready) throw new Error(result.message);
      setStage("recordings");
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function record(step: RecordingStep) {
    if (!session) return;
    setBusy(true);
    setError(undefined);
    setQuality(undefined);
    try {
      const duration =
        step.kind === "ordinary_speech"
          ? 12
          : step.kind === "hard_negative"
            ? 4
            : 2.4;
      const result = await sendSample(
        credential,
        session.session_id,
        step.id,
        await capturePcm(duration, microphone?.id),
      );
      setQuality(result);
      if (result.accepted) {
        window.setTimeout(() => {
          setQuality(undefined);
          setRecordingIndex((index) => index + 1);
        }, 650);
      }
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function train() {
    if (!session) return;
    setBusy(true);
    setError(undefined);
    try {
      const refreshed = await getOnboardingSession(
        credential,
        session.session_id,
      );
      setSession(refreshed);
      const missingIndex = refreshed.curriculum.findIndex(
        (item) => !refreshed.accepted_step_ids.includes(item.id),
      );
      if (missingIndex >= 0) {
        setRecordingIndex(missingIndex);
        setStage("recordings");
        setError(
          `Brakuje ${refreshed.curriculum.length - refreshed.accepted_step_ids.length} próbek. Zachowane nagrania pozostają bez zmian.`,
        );
        return;
      }
      const next = await startTraining(credential, session.session_id);
      setJob(next);
      setStage("training");
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function stopTraining() {
    if (!job) return;
    setBusy(true);
    try {
      await cancelTraining(credential, job.job_id);
      setJob(undefined);
      setStage("recordings");
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function validate(kind: "positive" | "negative") {
    if (!session) return;
    setBusy(true);
    setError(undefined);
    try {
      const duration = kind === "positive" ? 3 : 20;
      const result = await sendValidation(
        credential,
        session.session_id,
        kind,
        await capturePcm(duration, microphone?.id),
      );
      setMetrics(result);
      setSensitivity(result.threshold);
      if (kind === "positive") setPositiveAttempts((value) => value + 1);
      else setNegativeDone(true);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    if (!session) return;
    setBusy(true);
    try {
      await activateWakeModel(
        credential,
        session.session_id,
        metrics ? !metrics.passed : false,
      );
      await setWakeSensitivity(credential, sensitivity);
      setStage("ready");
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function close() {
    if (session) {
      try {
        await cancelOnboarding(credential, session.session_id);
      } catch (reason) {
        setError(message(reason));
        return;
      }
    }
    onClose?.();
  }

  return (
    <main
      className="onboarding overlay-surface"
      aria-label="Konfiguracja imienia asystenta"
    >
      <header className="onboarding__top drag-region" data-tauri-drag-region>
        <span>
          {stage === "welcome"
            ? "Pierwsze uruchomienie"
            : `Konfiguracja · ${progress}%`}
        </span>
        {onClose ? (
          <button
            className="icon-button"
            onClick={() => void close()}
            aria-label="Zamknij konfigurację"
            data-tauri-drag-region="false"
          >
            <X size={18} />
          </button>
        ) : (
          <span />
        )}
      </header>
      <div className="onboarding__progress" aria-hidden="true">
        <span style={{ width: `${progress}%` }} />
      </div>
      <section className="onboarding__content">
        {stage === "welcome" && (
          <>
            <div className="voice-line">
              <span />
              <span />
              <span />
              <span />
              <span />
            </div>
            <p className="onboarding__kicker">Twój głos. Twoje imię.</p>
            <h1>Najpierw nauczymy asystenta, kiedy ma się odezwać.</h1>
            <p className="onboarding__lead">
              Nagrania zostają na tym komputerze. Po zbudowaniu modelu usuniemy
              je automatycznie.
            </p>
            <button className="primary-action" onClick={() => setStage("name")}>
              Zacznij <ChevronRight size={18} />
            </button>
          </>
        )}
        {stage === "name" && (
          <>
            <button className="text-back" onClick={() => setStage("welcome")}>
              <ArrowLeft size={16} /> Wróć
            </button>
            <p className="onboarding__kicker">Imię asystenta</p>
            <h1>Jak chcesz go przywoływać?</h1>
            <p className="onboarding__lead">
              Najlepiej sprawdza się wyraźne imię z dwiema lub trzema sylabami.
            </p>
            <input
              className="name-input"
              autoFocus
              value={name}
              maxLength={32}
              onChange={(event) => setName(event.target.value)}
              placeholder="Wpisz własne imię"
            />
            <button
              className="primary-action"
              disabled={name.trim().length < 2 || busy}
              onClick={() => void createSession()}
            >
              Sprawdź imię <ChevronRight size={18} />
            </button>
          </>
        )}
        {stage === "assessment" && session && (
          <>
            <p className="onboarding__kicker">Ocena rozpoznawalności</p>
            <div className="score">
              <strong>{session.name.score}</strong>
              <span>/ 100</span>
            </div>
            <h1>
              „{session.name.display_name}” — {session.name.rating}
            </h1>
            <p className="onboarding__lead">{session.name.explanation}</p>
            <dl className="assessment-list">
              <div>
                <dt>Sylaby</dt>
                <dd>{session.name.syllable_count}</dd>
              </div>
              <div>
                <dt>Ryzyko pomyłek</dt>
                <dd>{session.name.false_trigger_risk}</dd>
              </div>
            </dl>
            {session.name.warnings.map((warning) => (
              <p className="inline-warning" key={warning}>
                {warning}
              </p>
            ))}
            <button
              className="primary-action"
              disabled={busy}
              onClick={() => void discoverMicrophones()}
            >
              Użyj tego imienia <ChevronRight size={18} />
            </button>
          </>
        )}
        {stage === "microphone" && (
          <>
            <p className="onboarding__kicker">Mikrofon</p>
            <h1>Wybierz urządzenie, którego używasz na co dzień.</h1>
            <div className="choice-list">
              {microphones.map((item) => (
                <button
                  className={
                    item.id === microphone?.id
                      ? "choice choice--active"
                      : "choice"
                  }
                  key={item.id}
                  onClick={() => setMicrophone(item)}
                >
                  <Mic size={18} />
                  <span>{item.label}</span>
                  {item.id === microphone?.id && <Check size={17} />}
                </button>
              ))}
            </div>
            <button
              className="primary-action"
              disabled={!microphone}
              onClick={() => setStage("calibration")}
            >
              Kalibruj mikrofon <ChevronRight size={18} />
            </button>
          </>
        )}
        {stage === "calibration" && (
          <>
            <p className="onboarding__kicker">Kalibracja</p>
            <h1>Mów normalnie przez 3 sekundy.</h1>
            <p className="onboarding__lead">
              Usiądź jak zwykle, około 50–80 cm od mikrofonu. Powiedz dowolne
              zdanie zwykłym głosem.
            </p>
            <RecordingPulse active={busy} />
            <button
              className="primary-action"
              disabled={busy}
              onClick={() => void calibrate()}
            >
              <Mic size={18} /> {busy ? "Słucham…" : "Rozpocznij kalibrację"}
            </button>
          </>
        )}
        {stage === "recordings" && step && (
          <RecordingScreen
            step={step}
            index={recordingIndex}
            total={session!.curriculum.length}
            busy={busy}
            quality={quality}
            onRecord={() => void record(step)}
          />
        )}
        {stage === "recordings" && !step && (
          <>
            <Sparkles size={28} />
            <p className="onboarding__kicker">Próbki gotowe</p>
            <h1>Mamy wystarczająco dużo różnych przykładów.</h1>
            <p className="onboarding__lead">
              Teraz zbudujemy lokalny model dopasowany do Twojego głosu i
              otoczenia.
            </p>
            <button
              className="primary-action"
              disabled={busy}
              onClick={() => void train()}
            >
              Zbuduj model <ChevronRight size={18} />
            </button>
          </>
        )}
        {stage === "training" && job && (
          <>
            <p className="onboarding__kicker">Trening lokalny</p>
            <h1>{job.stage}</h1>
            <p className="onboarding__lead">
              To może potrwać kilka minut. Okno pozostaje responsywne.
            </p>
            <div className="training-meter">
              <span style={{ width: `${job.progress}%` }} />
            </div>
            <strong className="training-percent">{job.progress}%</strong>
            <button
              className="secondary-action"
              disabled={busy}
              onClick={() => void stopTraining()}
            >
              <CircleStop size={17} /> Anuluj trening
            </button>
          </>
        )}
        {stage === "validation" && (
          <>
            <p className="onboarding__kicker">Próba działania</p>
            <h1>
              {positiveAttempts < 5
                ? `Powiedz „${name}” kiedy chcesz.`
                : "Teraz zwykła rozmowa bez imienia."}
            </h1>
            <p className="onboarding__lead">
              Nie klikaj w chwili wypowiadania imienia. Model działa dokładnie
              tak jak później w tle.
            </p>
            <RecordingPulse active={busy} />
            <button
              className="primary-action"
              disabled={busy}
              onClick={() =>
                void validate(positiveAttempts < 5 ? "positive" : "negative")
              }
            >
              <Mic size={18} />{" "}
              {positiveAttempts < 5
                ? `Próba ${positiveAttempts + 1} z 5`
                : "20 sekund testu negatywnego"}
            </button>
            {metrics && (
              <p className="validation-note">
                Wykrycia: {metrics.successful_activations}/{metrics.attempts} ·
                przypadkowe: {metrics.false_accepts}
              </p>
            )}
            {positiveAttempts >= 5 && negativeDone && (
              <button
                className="secondary-action"
                onClick={() => setStage("sensitivity")}
              >
                Przejdź do czułości
              </button>
            )}
          </>
        )}
        {stage === "sensitivity" && (
          <>
            <p className="onboarding__kicker">Czułość</p>
            <h1>Jak łatwo asystent ma reagować?</h1>
            <p className="onboarding__lead">
              Ustawiliśmy wartość na podstawie prób. Wyżej oznacza łatwiejszą
              aktywację, ale także większe ryzyko pomyłek.
            </p>
            <input
              className="sensitivity"
              type="range"
              min="0.2"
              max="0.85"
              step="0.01"
              value={sensitivity}
              onChange={(event) => setSensitivity(Number(event.target.value))}
            />
            <div className="range-labels">
              <span>Mniej przypadkowych</span>
              <strong>{Math.round(sensitivity * 100)}%</strong>
              <span>Łatwiejsze budzenie</span>
            </div>
            <button
              className="primary-action"
              onClick={() => setStage("voice")}
            >
              Dalej <ChevronRight size={18} />
            </button>
          </>
        )}
        {stage === "voice" && (
          <>
            <Volume2 size={28} />
            <p className="onboarding__kicker">Głos odpowiedzi</p>
            <h1>Polski głos jest gotowy.</h1>
            <p className="onboarding__lead">
              Możesz później wyłączyć odpowiedzi głosowe lub zmienić dostępny
              lokalny głos w ustawieniach.
            </p>
            <button
              className="primary-action"
              disabled={busy}
              onClick={() => void finish()}
            >
              Zapisz i aktywuj <Check size={18} />
            </button>
          </>
        )}
        {stage === "ready" && (
          <>
            <div className="ready-mark">
              <ShieldCheck size={30} />
            </div>
            <p className="onboarding__kicker">Gotowe</p>
            <h1>Powiedz „{name}”, aby zacząć.</h1>
            <p className="onboarding__lead">
              Model jest zapisany lokalnie i będzie aktywny po ponownym
              uruchomieniu.
            </p>
            <button className="primary-action" onClick={() => onComplete(name)}>
              Otwórz asystenta <ChevronRight size={18} />
            </button>
          </>
        )}
        {error && (
          <div className="onboarding-error" role="alert">
            <span>{error}</span>
            <button onClick={() => setError(undefined)}>
              <RotateCcw size={15} /> Spróbuj ponownie
            </button>
          </div>
        )}
      </section>
    </main>
  );
}

function RecordingPulse({ active }: { active: boolean }) {
  return (
    <div
      className={
        active ? "recording-pulse recording-pulse--active" : "recording-pulse"
      }
    >
      <Mic size={23} />
    </div>
  );
}

function RecordingScreen({
  step,
  index,
  total,
  busy,
  quality,
  onRecord,
}: {
  step: RecordingStep;
  index: number;
  total: number;
  busy: boolean;
  quality?: SampleQuality;
  onRecord: () => void;
}) {
  const negative =
    step.kind === "hard_negative" || step.kind === "ordinary_speech";
  return (
    <>
      <p className="onboarding__kicker">
        {negative ? "Przykład nieaktywujący" : "Przykład aktywujący"} ·{" "}
        {index + 1}/{total}
      </p>
      <p className="say-label">Powiedz dokładnie</p>
      <h1 className="phrase">„{step.phrase}”</h1>
      <dl className="guidance">
        <div>
          <dt>Głośność</dt>
          <dd>{step.loudness}</dd>
        </div>
        <div>
          <dt>Odległość</dt>
          <dd>{step.distance}</dd>
        </div>
        <div>
          <dt>Ton</dt>
          <dd>{step.intonation}</dd>
        </div>
        <div>
          <dt>Pozycja</dt>
          <dd>{step.posture}</dd>
        </div>
      </dl>
      <p className="sample-purpose">{step.guidance}</p>
      <p className="sample-avoid">Unikaj: {step.avoid}</p>
      {quality && (
        <div className={quality.accepted ? "quality quality--ok" : "quality"}>
          <strong>
            {quality.accepted ? "Próbka przyjęta" : "Powtórz próbkę"}
          </strong>
          <span>{quality.reason}</span>
          <small>
            {quality.speech_detected ? "✓ Głos wykryty" : "— Brak głosu"} ·{" "}
            {quality.volume_ok
              ? "✓ Głośność prawidłowa"
              : "— Głośność do poprawy"}{" "}
            ·{" "}
            {quality.no_clipping ? "✓ Brak przesterowania" : "— Przesterowanie"}
          </small>
        </div>
      )}
      <button className="primary-action" disabled={busy} onClick={onRecord}>
        <Mic size={18} />{" "}
        {busy
          ? "Nagrywam…"
          : quality && !quality.accepted
            ? "Nagraj ponownie"
            : "Nagraj próbkę"}
      </button>
    </>
  );
}

function message(reason: unknown): string {
  return reason instanceof Error
    ? reason.message
    : "Nie udało się wykonać tego kroku.";
}
