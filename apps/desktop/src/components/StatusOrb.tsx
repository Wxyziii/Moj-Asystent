import type { AssistantStateDefinition } from "../domain/assistant";

interface StatusOrbProps {
  definition: AssistantStateDefinition;
}

export function StatusOrb({ definition }: StatusOrbProps) {
  return (
    <span
      className={`status-orb status-orb--${definition.tone}`}
      aria-hidden="true"
    >
      <span />
    </span>
  );
}
