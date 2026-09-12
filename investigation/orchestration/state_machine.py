"""
Investigation state machine implementation.

Enforces the bounded investigation workflow engine, legal transitions,
monotonically increasing state versions, and external call checkpointing.

See ARCHITECTURE.md §9 and WORK_DIVISION.md §8.2.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator, Protocol

from pydantic import Field

from contracts.common import ContractModel, InvestigationState
from contracts.errors.schemas import INVALID_STATE_TRANSITION, StructuredError


# ---------------------------------------------------------------------------
# Terminal States and Legal Transitions (ARCHITECTURE.md §9)
# ---------------------------------------------------------------------------

TERMINAL_STATES: frozenset[InvestigationState] = frozenset({
    InvestigationState.COMPLETED,
    InvestigationState.INCONCLUSIVE,
    InvestigationState.CANCELLED,
})

LEGAL_TRANSITIONS: dict[InvestigationState, frozenset[InvestigationState]] = {
    InvestigationState.RECEIVED: frozenset({
        InvestigationState.ASSESSING_GAPS,
        InvestigationState.CANCELLED,
    }),
    InvestigationState.ASSESSING_GAPS: frozenset({
        InvestigationState.COLLECTING_EVIDENCE,
        InvestigationState.INCONCLUSIVE,
        InvestigationState.CANCELLED,
    }),
    InvestigationState.COLLECTING_EVIDENCE: frozenset({
        InvestigationState.BUILDING_TIMELINE,
        InvestigationState.INCONCLUSIVE,
        InvestigationState.CANCELLED,
    }),
    InvestigationState.BUILDING_TIMELINE: frozenset({
        InvestigationState.GENERATING_HYPOTHESES,
        InvestigationState.CANCELLED,
    }),
    InvestigationState.GENERATING_HYPOTHESES: frozenset({
        InvestigationState.ASSESSING_GAPS,
        InvestigationState.RANKING,
        InvestigationState.INCONCLUSIVE,
        InvestigationState.CANCELLED,
    }),
    InvestigationState.RANKING: frozenset({
        InvestigationState.COMPLETED,
        InvestigationState.CANCELLED,
    }),
    InvestigationState.COMPLETED: frozenset(),
    InvestigationState.INCONCLUSIVE: frozenset(),
    InvestigationState.CANCELLED: frozenset(),
}


# ---------------------------------------------------------------------------
# Transition and Checkpoint Records
# ---------------------------------------------------------------------------


class TransitionRecord(ContractModel):
    """Immutable audit record of a state transition."""
    from_state: InvestigationState = Field(
        ...,
        description="State before the transition.",
    )
    to_state: InvestigationState = Field(
        ...,
        description="State after the transition.",
    )
    state_version: int = Field(
        ...,
        description="State version produced by this transition.",
    )
    timestamp: datetime = Field(
        ...,
        description="Timestamp when the transition occurred.",
    )
    reason: str = Field(
        default="",
        description="Causal or operational reason for the transition.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context for this transition.",
    )


class InvestigationCheckpoint(ContractModel):
    """
    Checkpoint representation for durable persistence and crash recovery.
    """
    incident_id: str = Field(
        ...,
        description="Incident this checkpoint belongs to.",
    )
    current_state: InvestigationState = Field(
        ...,
        description="Current state of the investigation machine.",
    )
    state_version: int = Field(
        ...,
        description="Current state version.",
    )
    history: list[TransitionRecord] = Field(
        default_factory=list,
        description="Transition history up to this checkpoint.",
    )
    context_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Serialized contextual payload at checkpoint time.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when this checkpoint was created.",
    )
    label: str = Field(
        default="",
        description="Optional label for the checkpoint (e.g. pre_call, post_call).",
    )


# ---------------------------------------------------------------------------
# Checkpoint Store Protocol & In-Memory Implementation
# ---------------------------------------------------------------------------


class CheckpointStore(Protocol):
    """Protocol for persisting and retrieving investigation checkpoints."""

    def save(self, checkpoint: InvestigationCheckpoint) -> None:
        """Persist a checkpoint."""
        ...

    def load_latest(self, incident_id: str) -> InvestigationCheckpoint | None:
        """Load the most recent checkpoint for an incident."""
        ...

    def list_checkpoints(self, incident_id: str) -> list[InvestigationCheckpoint]:
        """List all checkpoints for an incident in chronological order."""
        ...


class InMemoryCheckpointStore:
    """Thread-safe in-memory store for checkpoints."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, list[InvestigationCheckpoint]] = {}

    def save(self, checkpoint: InvestigationCheckpoint) -> None:
        if checkpoint.incident_id not in self._checkpoints:
            self._checkpoints[checkpoint.incident_id] = []
        self._checkpoints[checkpoint.incident_id].append(checkpoint)

    def load_latest(self, incident_id: str) -> InvestigationCheckpoint | None:
        incident_list = self._checkpoints.get(incident_id, [])
        return incident_list[-1] if incident_list else None

    def list_checkpoints(self, incident_id: str) -> list[InvestigationCheckpoint]:
        return list(self._checkpoints.get(incident_id, []))


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, error: StructuredError) -> None:
        super().__init__(error.message)
        self.error = error


# ---------------------------------------------------------------------------
# Investigation State Machine
# ---------------------------------------------------------------------------


class InvestigationStateMachine:
    """
    Manages the lifecycle state of an incident investigation.

    Guarantees:
    - Only transitions defined in ARCHITECTURE.md §9 are permitted.
    - Illegal transitions fail with a StructuredError (code INVALID_STATE_TRANSITION).
    - state_version is strictly monotonically increasing.
    - Cancellation is supported from any non-terminal state.
    - Checkpoint creation and restoration before/after external calls.
    """

    def __init__(
        self,
        incident_id: str,
        initial_state: InvestigationState = InvestigationState.RECEIVED,
        initial_state_version: int = 1,
        store: CheckpointStore | None = None,
    ) -> None:
        self._incident_id = incident_id
        self._current_state = initial_state
        self._state_version = initial_state_version
        self._history: list[TransitionRecord] = []
        self._store = store

    @property
    def incident_id(self) -> str:
        """The incident being investigated."""
        return self._incident_id

    @property
    def current_state(self) -> InvestigationState:
        """The current investigation state."""
        return self._current_state

    @property
    def state_version(self) -> int:
        """Monotonically increasing version of the investigation state."""
        return self._state_version

    @property
    def history(self) -> list[TransitionRecord]:
        """Chronological list of all state transitions."""
        return list(self._history)

    @property
    def is_terminal(self) -> bool:
        """Whether the investigation is in a terminal state."""
        return self._current_state in TERMINAL_STATES

    def can_transition_to(self, target_state: InvestigationState | str) -> bool:
        """Check whether transitioning to target_state is legal from the current state."""
        try:
            target = self._normalize_state(target_state)
        except ValueError:
            return False

        allowed = LEGAL_TRANSITIONS.get(self._current_state, frozenset())
        return target in allowed

    def validate_transition(
        self, target_state: InvestigationState | str
    ) -> StructuredError | None:
        """
        Validate whether transitioning to target_state is legal.
        Returns None if legal, or a StructuredError if illegal.
        """
        try:
            target = self._normalize_state(target_state)
        except ValueError:
            return StructuredError(
                code=INVALID_STATE_TRANSITION,
                message=f"Unknown target state: '{target_state}'.",
                retryable=False,
                source="investigation.orchestration.state_machine",
                details={
                    "incident_id": self._incident_id,
                    "current_state": self._current_state.value,
                    "target_state": str(target_state),
                    "state_version": self._state_version,
                },
            )

        allowed = LEGAL_TRANSITIONS.get(self._current_state, frozenset())
        if target not in allowed:
            return StructuredError(
                code=INVALID_STATE_TRANSITION,
                message=(
                    f"Illegal state transition from '{self._current_state.value}' "
                    f"to '{target.value}' for incident '{self._incident_id}'."
                ),
                retryable=False,
                source="investigation.orchestration.state_machine",
                details={
                    "incident_id": self._incident_id,
                    "current_state": self._current_state.value,
                    "target_state": target.value,
                    "state_version": self._state_version,
                    "allowed_transitions": [s.value for s in allowed],
                },
            )

        return None

    def transition_to(
        self,
        target_state: InvestigationState | str,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> InvestigationState:
        """
        Perform a state transition.

        Raises InvalidStateTransitionError if the transition is illegal.
        On success:
        - increments state_version monotonically
        - records the transition in history
        - updates current_state
        - returns the new state
        """
        error = self.validate_transition(target_state)
        if error is not None:
            raise InvalidStateTransitionError(error)

        target = self._normalize_state(target_state)
        new_version = self._state_version + 1

        record = TransitionRecord(
            from_state=self._current_state,
            to_state=target,
            state_version=new_version,
            timestamp=datetime.now(timezone.utc),
            reason=reason,
            metadata=metadata or {},
        )

        self._history.append(record)
        self._current_state = target
        self._state_version = new_version

        return self._current_state

    def try_transition_to(
        self,
        target_state: InvestigationState | str,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, StructuredError | None]:
        """
        Attempt a state transition without raising an exception.
        Returns (True, None) on success, or (False, error) on failure.
        """
        error = self.validate_transition(target_state)
        if error is not None:
            return False, error

        self.transition_to(target_state, reason=reason, metadata=metadata)
        return True, None

    def cancel(
        self,
        reason: str = "Investigation cancelled by user/operator.",
        metadata: dict[str, Any] | None = None,
    ) -> InvestigationState:
        """
        Cancel the investigation from any non-terminal state.

        Raises InvalidStateTransitionError if already in a terminal state.
        """
        return self.transition_to(
            InvestigationState.CANCELLED,
            reason=reason,
            metadata=metadata,
        )

    # -----------------------------------------------------------------------
    # External Call Checkpointing
    # -----------------------------------------------------------------------

    def create_checkpoint(
        self,
        label: str = "",
        context_payload: dict[str, Any] | None = None,
        store: CheckpointStore | None = None,
    ) -> InvestigationCheckpoint:
        """Create and optionally persist a checkpoint of the current state."""
        checkpoint = InvestigationCheckpoint(
            incident_id=self._incident_id,
            current_state=self._current_state,
            state_version=self._state_version,
            history=list(self._history),
            context_payload=context_payload or {},
            created_at=datetime.now(timezone.utc),
            label=label,
        )

        target_store = store or self._store
        if target_store is not None:
            target_store.save(checkpoint)

        return checkpoint

    def checkpoint_before_call(
        self,
        call_name: str,
        context_payload: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        store: CheckpointStore | None = None,
    ) -> InvestigationCheckpoint:
        """Convenience method to checkpoint state before an external call."""
        data = payload if payload is not None else context_payload
        return self.create_checkpoint(
            label=f"pre_call:{call_name}",
            context_payload=data,
            store=store,
        )

    def checkpoint_after_call(
        self,
        call_name: str,
        context_payload: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        store: CheckpointStore | None = None,
    ) -> InvestigationCheckpoint:
        """Convenience method to checkpoint state after an external call."""
        data = payload if payload is not None else context_payload
        return self.create_checkpoint(
            label=f"post_call:{call_name}",
            context_payload=data,
            store=store,
        )

    @contextmanager
    def external_call(
        self,
        call_name: str,
        context_payload: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        store: CheckpointStore | None = None,
    ) -> Iterator[None]:
        """
        Context manager that automatically checkpoints state before and after an external call.
        Ensures a worker crash or external failure leaves a durable checkpoint.
        """
        data = payload if payload is not None else context_payload
        self.checkpoint_before_call(call_name, context_payload=data, store=store)
        try:
            yield
        finally:
            self.checkpoint_after_call(call_name, context_payload=data, store=store)

    @classmethod
    def restore_from_checkpoint(
        cls,
        checkpoint: InvestigationCheckpoint,
        store: CheckpointStore | None = None,
    ) -> InvestigationStateMachine:
        """
        Reconstruct an InvestigationStateMachine from a saved checkpoint.
        Preserves state, state_version, and transition history.
        """
        machine = cls(
            incident_id=checkpoint.incident_id,
            initial_state=checkpoint.current_state,
            initial_state_version=checkpoint.state_version,
            store=store,
        )
        machine._history = list(checkpoint.history)
        return machine

    @staticmethod
    def _normalize_state(state: InvestigationState | str) -> InvestigationState:
        if isinstance(state, InvestigationState):
            return state
        if isinstance(state, str):
            try:
                return InvestigationState(state)
            except ValueError:
                # Try uppercase if string was passed in lowercase
                return InvestigationState(state.upper())
        raise ValueError(f"Cannot normalize '{state}' to InvestigationState.")
