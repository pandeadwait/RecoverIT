"""Investigation orchestration — state machine, loop, and stopping rules."""

from investigation.orchestration.orchestrator import (
    CollectionService,
    ContextBuilder,
    InMemoryCollectionService,
    InMemoryContextBuilder,
    InvestigationOrchestrator,
    StoppingDecision,
    StoppingRuleEvaluator,
)
from investigation.orchestration.state_machine import (
    CheckpointStore,
    InMemoryCheckpointStore,
    InvalidStateTransitionError,
    InvestigationCheckpoint,
    InvestigationStateMachine,
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    TransitionRecord,
)

__all__ = [
    "CheckpointStore",
    "CollectionService",
    "ContextBuilder",
    "InMemoryCheckpointStore",
    "InMemoryCollectionService",
    "InMemoryContextBuilder",
    "InvalidStateTransitionError",
    "InvestigationCheckpoint",
    "InvestigationOrchestrator",
    "InvestigationStateMachine",
    "LEGAL_TRANSITIONS",
    "StoppingDecision",
    "StoppingRuleEvaluator",
    "TERMINAL_STATES",
    "TransitionRecord",
]
