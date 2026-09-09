import pytest

from moj_asystent_core.state import AssistantStateMachine, InvalidStateTransition


def test_state_machine_accepts_the_documented_listening_flow() -> None:
    machine = AssistantStateMachine()

    for target in (
        "wake_detected",
        "listening",
        "transcribing",
        "thinking",
        "speaking",
        "follow_up",
        "idle",
    ):
        event = machine.transition(target)
        assert event.payload.state == target


def test_state_machine_rejects_invalid_transitions_without_mutating_state() -> None:
    machine = AssistantStateMachine()

    with pytest.raises(InvalidStateTransition, match="idle -> speaking"):
        machine.transition("speaking")

    assert machine.state == "idle"


def test_state_machine_recovers_from_an_error_only_via_idle() -> None:
    machine = AssistantStateMachine()
    machine.transition("error")

    with pytest.raises(InvalidStateTransition):
        machine.transition("listening")

    machine.transition("idle")
    assert machine.state == "idle"
