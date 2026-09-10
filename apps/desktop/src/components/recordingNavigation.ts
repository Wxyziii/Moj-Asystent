import type { RecordingStep } from "@moj-asystent/protocol";

export function findNextMissingRecordingIndex(
  curriculum: RecordingStep[],
  acceptedStepIds: string[],
  startAt = 0,
): number {
  const accepted = new Set(acceptedStepIds);
  return curriculum.findIndex(
    (step, index) => index >= startAt && !accepted.has(step.id),
  );
}
