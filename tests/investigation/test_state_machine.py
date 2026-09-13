"""
Unit tests for InvestigationStateMachine.

Verifies:
- Every legal state transition succeeds.
- Every illegal state transition is rejected with StructuredError (code INVALID_STATE_TRANSITION).
- State version is monotonically increasing on every transition.
- Cancellation works from every non-terminal state and fails on terminal states.
- Checkpointing and restoration before/after external calls work as specified.

See ARCHITECTURE.md §9 and WORK_DIVISION.md §8.2.
"""

from __future__ import annotations

import pytest

from contracts.common import InvestigationState
from contracts.errors.schemas import INVALID_STATE_TRANSITION
from investigation.orchestration.state_machine import (
    InMemoryCheckpointStore,
    InvalidStateTransitionError,
    InvestigationCheckpoint,
    InvestigationStateMachine,
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
)


# ---------------------------------------------------------------------------
# Initial State and Property Tests
# ---------------------------------------------------------------------------


def test_initial_state_defaults() -> None:
    sm = InvestigationStateMachine(incident_id="inc_001")
    assert sm.incident_id == "inc_001"
    assert sm.current_state == InvestigationState.RECEIVED
    assert sm.state_version == 1
    assert len(sm.history) == 0
    assert not sm.is_terminal


def test_custom_initial_state() -> None:
    sm = InvestigationStateMachine(
        incident_id="inc_002",
        initial_state=InvestigationState.ASSESSING_GAPS,
        initial_state_version=5,
    )
    assert sm.current_state == InvestigationState.ASSESSING_GAPS
    assert sm.state_version == 5


# ---------------------------------------------------------------------------
# Legal State Transitions (ARCHITECTURE.md §9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (InvestigationState.RECEIVED, InvestigationState.ASSESSING_GAPS),
        (InvestigationState.RECEIVED, InvestigationState.CANCELLED),
        (InvestigationState.ASSESSING_GAPS, InvestigationState.COLLECTING_EVIDENCE),
        (InvestigationState.ASSESSING_GAPS, InvestigationState.INCONCLUSIVE),
        (InvestigationState.ASSESSING_GAPS, InvestigationState.CANCELLED),
        (InvestigationState.COLLECTING_EVIDENCE, InvestigationState.BUILDING_TIMELINE),
        (InvestigationState.COLLECTING_EVIDENCE, InvestigationState.INCONCLUSIVE),
        (InvestigationState.COLLECTING_EVIDENCE, InvestigationState.CANCELLED),
        (InvestigationState.BUILDING_TIMELINE, InvestigationState.GENERATING_HYPOTHESES),
        (InvestigationState.BUILDING_TIMELINE, InvestigationState.CANCELLED),
        (InvestigationState.GENERATING_HYPOTHESES, InvestigationState.ASSESSING_GAPS),
        (InvestigationState.GENERATING_HYPOTHESES, InvestigationState.RANKING),
        (InvestigationState.GENERATING_HYPOTHESES, InvestigationState.INCONCLUSIVE),
        (InvestigationState.GENERATING_HYPOTHESES, InvestigationState.CANCELLED),
        (InvestigationState.RANKING, InvestigationState.COMPLETED),
        (InvestigationState.RANKING, InvestigationState.CANCELLED),
    ],
)
def test_all_legal_transitions_succeed(
    from_state: InvestigationState, to_state: InvestigationState
) -> None:
    sm = InvestigationStateMachine(
        incident_id="inc_legal",
        initial_state=from_state,
        initial_state_version=10,
    )

    assert sm.can_transition_to(to_state)
    assert sm.validate_transition(to_state) is None

    result = sm.transition_to(to_state, reason=f"testing {from_state} -> {to_state}")
    assert result == to_state
    assert sm.current_state == to_state
    assert sm.state_version == 11
    assert len(sm.history) == 1
    assert sm.history[0].from_state == from_state
    assert sm.history[0].to_state == to_state
    assert sm.history[0].state_version == 11


def test_full_investigation_lifecycle_with_multiple_rounds() -> None:
    sm = InvestigationStateMachine(incident_id="inc_lifecycle")

    # Round 1
    sm.transition_to(InvestigationState.ASSESSING_GAPS, reason="initial assessment")
    assert sm.state_version == 2

    sm.transition_to(InvestigationState.COLLECTING_EVIDENCE, reason="query plan issued")
    assert sm.state_version == 3

    sm.transition_to(InvestigationState.BUILDING_TIMELINE, reason="evidence batch received")
    assert sm.state_version == 4

    sm.transition_to(InvestigationState.GENERATING_HYPOTHESES, reason="initial hypotheses")
    assert sm.state_version == 5

    # Round 2: more evidence needed
    sm.transition_to(InvestigationState.ASSESSING_GAPS, reason="more evidence required")
    assert sm.state_version == 6

    sm.transition_to(InvestigationState.COLLECTING_EVIDENCE, reason="second query plan")
    assert sm.state_version == 7

    sm.transition_to(InvestigationState.BUILDING_TIMELINE, reason="second evidence batch")
    assert sm.state_version == 8

    sm.transition_to(InvestigationState.GENERATING_HYPOTHESES, reason="revised hypotheses")
    assert sm.state_version == 9

    # Sufficient evidence -> ranking -> completed
    sm.transition_to(InvestigationState.RANKING, reason="sufficient evidence")
    assert sm.state_version == 10

    sm.transition_to(InvestigationState.COMPLETED, reason="ranked hypothesis set stored")
    assert sm.state_version == 11
    assert sm.is_terminal
    assert len(sm.history) == 10


# ---------------------------------------------------------------------------
# Illegal State Transitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("from_state", "illegal_target"),
    [
        (InvestigationState.RECEIVED, InvestigationState.COLLECTING_EVIDENCE),
        (InvestigationState.RECEIVED, InvestigationState.BUILDING_TIMELINE),
        (InvestigationState.RECEIVED, InvestigationState.GENERATING_HYPOTHESES),
        (InvestigationState.RECEIVED, InvestigationState.RANKING),
        (InvestigationState.RECEIVED, InvestigationState.COMPLETED),
        (InvestigationState.RECEIVED, InvestigationState.INCONCLUSIVE),
        (InvestigationState.ASSESSING_GAPS, InvestigationState.RECEIVED),
        (InvestigationState.ASSESSING_GAPS, InvestigationState.BUILDING_TIMELINE),
        (InvestigationState.ASSESSING_GAPS, InvestigationState.RANKING),
        (InvestigationState.ASSESSING_GAPS, InvestigationState.COMPLETED),
        (InvestigationState.COLLECTING_EVIDENCE, InvestigationState.RECEIVED),
        (InvestigationState.COLLECTING_EVIDENCE, InvestigationState.ASSESSING_GAPS),
        (InvestigationState.COLLECTING_EVIDENCE, InvestigationState.GENERATING_HYPOTHESES),
        (InvestigationState.COLLECTING_EVIDENCE, InvestigationState.RANKING),
        (InvestigationState.COLLECTING_EVIDENCE, InvestigationState.COMPLETED),
        (InvestigationState.BUILDING_TIMELINE, InvestigationState.ASSESSING_GAPS),
        (InvestigationState.BUILDING_TIMELINE, InvestigationState.COLLECTING_EVIDENCE),
        (InvestigationState.BUILDING_TIMELINE, InvestigationState.RANKING),
        (InvestigationState.BUILDING_TIMELINE, InvestigationState.COMPLETED),
        (InvestigationState.BUILDING_TIMELINE, InvestigationState.INCONCLUSIVE),
        (InvestigationState.GENERATING_HYPOTHESES, InvestigationState.RECEIVED),
        (InvestigationState.GENERATING_HYPOTHESES, InvestigationState.COLLECTING_EVIDENCE),
        (InvestigationState.GENERATING_HYPOTHESES, InvestigationState.BUILDING_TIMELINE),
        (InvestigationState.GENERATING_HYPOTHESES, InvestigationState.COMPLETED),
        (InvestigationState.RANKING, InvestigationState.RECEIVED),
        (InvestigationState.RANKING, InvestigationState.ASSESSING_GAPS),
        (InvestigationState.RANKING, InvestigationState.COLLECTING_EVIDENCE),
        (InvestigationState.RANKING, InvestigationState.BUILDING_TIMELINE),
        (InvestigationState.RANKING, InvestigationState.GENERATING_HYPOTHESES),
        (InvestigationState.COMPLETED, InvestigationState.RECEIVED),
        (InvestigationState.COMPLETED, InvestigationState.ASSESSING_GAPS),
        (InvestigationState.COMPLETED, InvestigationState.CANCELLED),
        (InvestigationState.INCONCLUSIVE, InvestigationState.ASSESSING_GAPS),
        (InvestigationState.INCONCLUSIVE, InvestigationState.CANCELLED),
        (InvestigationState.CANCELLED, InvestigationState.RECEIVED),
        (InvestigationState.CANCELLED, InvestigationState.COMPLETED),
    ],
)
def test_illegal_transitions_rejected_with_structured_error(
    from_state: InvestigationState, illegal_target: InvestigationState
) -> None:
    sm = InvestigationStateMachine(incident_id="inc_illegal", initial_state=from_state)

    assert not sm.can_transition_to(illegal_target)

    error = sm.validate_transition(illegal_target)
    assert error is not None
    assert error.code == INVALID_STATE_TRANSITION
    assert error.source == "investigation.orchestration.state_machine"
    assert error.details["incident_id"] == "inc_illegal"
    assert error.details["current_state"] == from_state.value
    assert error.details["target_state"] == illegal_target.value

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        sm.transition_to(illegal_target)

    assert exc_info.value.error.code == INVALID_STATE_TRANSITION
    assert exc_info.value.error.details["current_state"] == from_state.value


def test_try_transition_to_returns_false_and_error_on_illegal() -> None:
    sm = InvestigationStateMachine(incident_id="inc_try")
    ok, error = sm.try_transition_to(InvestigationState.COMPLETED)
    assert not ok
    assert error is not None
    assert error.code == INVALID_STATE_TRANSITION


def test_try_transition_to_succeeds_on_legal() -> None:
    sm = InvestigationStateMachine(incident_id="inc_try")
    ok, error = sm.try_transition_to(InvestigationState.ASSESSING_GAPS)
    assert ok
    assert error is None
    assert sm.current_state == InvestigationState.ASSESSING_GAPS


def test_unknown_target_state_rejected() -> None:
    sm = InvestigationStateMachine(incident_id="inc_unknown")
    assert not sm.can_transition_to("NOT_A_REAL_STATE")
    error = sm.validate_transition("NOT_A_REAL_STATE")
    assert error is not None
    assert error.code == INVALID_STATE_TRANSITION

    with pytest.raises(InvalidStateTransitionError):
        sm.transition_to("NOT_A_REAL_STATE")


# ---------------------------------------------------------------------------
# State Version Monotonicity
# ---------------------------------------------------------------------------


def test_state_version_strictly_monotonically_increases() -> None:
    sm = InvestigationStateMachine(incident_id="inc_version", initial_state_version=100)
    versions = [sm.state_version]

    transitions = [
        InvestigationState.ASSESSING_GAPS,
        InvestigationState.COLLECTING_EVIDENCE,
        InvestigationState.BUILDING_TIMELINE,
        InvestigationState.GENERATING_HYPOTHESES,
        InvestigationState.RANKING,
        InvestigationState.COMPLETED,
    ]

    for target in transitions:
        sm.transition_to(target)
        versions.append(sm.state_version)

    assert len(versions) == 7
    # Verify strict monotonic increase
    for i in range(len(versions) - 1):
        assert versions[i + 1] == versions[i] + 1


# ---------------------------------------------------------------------------
# Cancellation Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "non_terminal_state",
    [
        InvestigationState.RECEIVED,
        InvestigationState.ASSESSING_GAPS,
        InvestigationState.COLLECTING_EVIDENCE,
        InvestigationState.BUILDING_TIMELINE,
        InvestigationState.GENERATING_HYPOTHESES,
        InvestigationState.RANKING,
    ],
)
def test_cancellation_works_from_every_non_terminal_state(
    non_terminal_state: InvestigationState,
) -> None:
    sm = InvestigationStateMachine(
        incident_id="inc_cancel",
        initial_state=non_terminal_state,
        initial_state_version=3,
    )
    assert not sm.is_terminal

    cancelled = sm.cancel(reason="Operator aborted")
    assert cancelled == InvestigationState.CANCELLED
    assert sm.current_state == InvestigationState.CANCELLED
    assert sm.is_terminal
    assert sm.state_version == 4
    assert sm.history[-1].reason == "Operator aborted"


@pytest.mark.parametrize(
    "terminal_state",
    [
        InvestigationState.COMPLETED,
        InvestigationState.INCONCLUSIVE,
        InvestigationState.CANCELLED,
    ],
)
def test_cancellation_fails_from_terminal_states(
    terminal_state: InvestigationState,
) -> None:
    sm = InvestigationStateMachine(
        incident_id="inc_term",
        initial_state=terminal_state,
    )
    assert sm.is_terminal

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        sm.cancel()

    assert exc_info.value.error.code == INVALID_STATE_TRANSITION


# ---------------------------------------------------------------------------
# Checkpointing and Crash Recovery
# ---------------------------------------------------------------------------


def test_checkpoint_and_restore() -> None:
    store = InMemoryCheckpointStore()
    sm = InvestigationStateMachine(incident_id="inc_restore", store=store)

    sm.transition_to(InvestigationState.ASSESSING_GAPS, reason="start")
    sm.transition_to(InvestigationState.COLLECTING_EVIDENCE, reason="query")

    checkpoint = sm.create_checkpoint(
        label="checkpoint_1", context_payload={"round": 1, "active_hyp": ["hyp_01"]}
    )

    assert checkpoint.incident_id == "inc_restore"
    assert checkpoint.current_state == InvestigationState.COLLECTING_EVIDENCE
    assert checkpoint.state_version == 3
    assert len(checkpoint.history) == 2
    assert checkpoint.context_payload["round"] == 1

    # Simulate worker restart: restore from checkpoint
    latest = store.load_latest("inc_restore")
    assert latest is not None
    restored_sm = InvestigationStateMachine.restore_from_checkpoint(latest, store=store)

    assert restored_sm.incident_id == "inc_restore"
    assert restored_sm.current_state == InvestigationState.COLLECTING_EVIDENCE
    assert restored_sm.state_version == 3
    assert len(restored_sm.history) == 2

    # Resumed machine continues transitions smoothly
    restored_sm.transition_to(InvestigationState.BUILDING_TIMELINE, reason="resumed")
    assert restored_sm.current_state == InvestigationState.BUILDING_TIMELINE
    assert restored_sm.state_version == 4
    assert len(restored_sm.history) == 3


def test_external_call_context_manager() -> None:
    store = InMemoryCheckpointStore()
    sm = InvestigationStateMachine(incident_id="inc_ext", store=store)
    sm.transition_to(InvestigationState.ASSESSING_GAPS)

    payload = {"provider": "llm_gateway", "model": "gemini-pro"}

    with sm.external_call("assess_missing_info", payload=payload, store=store):
        # Inside external call
        pass

    checkpoints = store.list_checkpoints("inc_ext")
    assert len(checkpoints) == 2
    assert checkpoints[0].label == "pre_call:assess_missing_info"
    assert checkpoints[1].label == "post_call:assess_missing_info"
    assert checkpoints[0].context_payload == payload
    assert checkpoints[1].context_payload == payload
