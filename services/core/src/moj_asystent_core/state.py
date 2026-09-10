"""Deterministic, side-effect-free assistant UI state transitions."""

from .protocol import AssistantState, AssistantStateChanged, AssistantStateChangedPayload, new_event


class InvalidStateTransition(ValueError):
    """Raised without changing state when a caller requests an invalid transition."""


_TRANSITIONS: dict[AssistantState, frozenset[AssistantState]] = {
    "idle": frozenset({"wake_detected", "listening", "error"}),
    "wake_detected": frozenset({"listening", "idle", "error"}),
    "listening": frozenset({"transcribing", "idle", "error"}),
    "transcribing": frozenset({"thinking", "idle", "error"}),
    "thinking": frozenset({"speaking", "follow_up", "idle", "error"}),
    "speaking": frozenset({"follow_up", "idle", "error"}),
    "follow_up": frozenset({"listening", "idle", "error"}),
    "error": frozenset({"idle"}),
}


class AssistantStateMachine:
    def __init__(self, initial_state: AssistantState = "idle") -> None:
        if initial_state not in _TRANSITIONS:
            raise InvalidStateTransition("Unknown initial assistant state")
        self._state = initial_state

    @property
    def state(self) -> AssistantState:
        return self._state

    def transition(self, target: AssistantState) -> AssistantStateChanged:
        if target == self._state:
            return _state_event(self._state, target)
        if target not in _TRANSITIONS[self._state]:
            raise InvalidStateTransition(
                f"Invalid assistant state transition: {self._state} -> {target}"
            )
        previous_state = self._state
        event = _state_event(previous_state, target)
        self._state = target
        return event

    def synchronize(self) -> AssistantStateChanged:
        return _state_event(None, self._state)


def _state_event(
    previous_state: AssistantState | None, state: AssistantState
) -> AssistantStateChanged:
    event = new_event(
        "assistant.state.changed",
        AssistantStateChangedPayload(previous_state=previous_state, state=state),
    )
    if not isinstance(event, AssistantStateChanged):
        raise RuntimeError("Expected assistant.state.changed event")
    return event
