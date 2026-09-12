"""
Recorded reasoning provider for deterministic replay testing.

Replays pre-recorded responses from fixture files or memory dictionaries.
Guarantees identical behavior across test runs without external dependencies.

See WORK_DIVISION.md §8.7 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contracts.collection.schemas import SourceCapabilityCatalog
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import Hypothesis, HypothesisSet
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    InvestigationBudget,
    MissingInformationAssessment,
)


class UnrecordedRequestError(KeyError):
    """Raised when an unrecorded request is made to RecordedReasoningProvider."""


class RecordedReasoningProvider:
    """
    Replay provider that matches incoming requests to pre-recorded responses.

    Matches requests by:
    1. Composite key: '{incident_id}:{method_name}:{round}'
    2. Composite key: '{incident_id}:{method_name}'
    3. Method key with round: '{method_name}:{round}'
    4. Method key: '{method_name}'
    5. Sequential queue under '{method_name}' list
    """

    def __init__(
        self,
        recordings: dict[str, Any] | None = None,
        fixture_path: str | Path | None = None,
    ) -> None:
        self._recordings: dict[str, Any] = {}
        self._call_counts: dict[str, int] = {}
        self._calls: list[dict[str, Any]] = []

        if fixture_path is not None:
            self.load_fixture(fixture_path)
        if recordings is not None:
            self._recordings.update(recordings)

    @classmethod
    def from_file(cls, path: str | Path) -> RecordedReasoningProvider:
        """Construct a RecordedReasoningProvider from a JSON fixture file."""
        return cls(fixture_path=path)

    @property
    def call_count(self) -> int:
        """Total number of replayed calls."""
        return len(self._calls)

    @property
    def calls(self) -> list[dict[str, Any]]:
        """List of all replayed call records."""
        return list(self._calls)

    def load_fixture(self, fixture_path: str | Path) -> None:
        """Load pre-recorded JSON responses from a file."""
        path = Path(fixture_path)
        if not path.exists():
            raise FileNotFoundError(f"Recorded fixture file not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(
                    f"Fixture file must contain a JSON object at root, got {type(data)}."
                )
            self._recordings.update(data)

    def add_recording(self, key: str, response: Any) -> None:
        """Add or overwrite a recorded response."""
        self._recordings[key] = response

    # -----------------------------------------------------------------------
    # ReasoningProvider implementation
    # -----------------------------------------------------------------------

    async def assess_missing_information(
        self,
        incident: IncidentSeed,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        active_hypotheses: list[Hypothesis],
    ) -> MissingInformationAssessment:
        method = "assess_missing_information"
        call_num = self._increment_call(method)
        round_num = getattr(context, "round", call_num)

        raw = self._lookup(
            method=method,
            incident_id=incident.incident_id,
            round_num=round_num,
            call_num=call_num,
        )

        self._calls.append({
            "method": method,
            "incident_id": incident.incident_id,
            "round": round_num,
        })

        if isinstance(raw, MissingInformationAssessment):
            return raw
        return MissingInformationAssessment.model_validate(raw)

    async def plan_queries(
        self,
        missing_information: MissingInformationAssessment,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        budget: InvestigationBudget,
    ) -> EvidenceQueryPlan:
        method = "plan_queries"
        call_num = self._increment_call(method)
        round_num = getattr(context, "round", call_num)

        raw = self._lookup(
            method=method,
            incident_id=missing_information.incident_id,
            round_num=round_num,
            call_num=call_num,
        )

        self._calls.append({
            "method": method,
            "incident_id": missing_information.incident_id,
            "round": round_num,
        })

        if isinstance(raw, EvidenceQueryPlan):
            return raw
        return EvidenceQueryPlan.model_validate(raw)

    async def generate_hypotheses(
        self,
        incident: IncidentSeed,
        context: IncidentContextSnapshot,
        limits: InvestigationBudget,
    ) -> HypothesisSet:
        method = "generate_hypotheses"
        call_num = self._increment_call(method)
        round_num = getattr(context, "round", call_num)

        raw = self._lookup(
            method=method,
            incident_id=incident.incident_id,
            round_num=round_num,
            call_num=call_num,
        )

        self._calls.append({
            "method": method,
            "incident_id": incident.incident_id,
            "round": round_num,
        })

        if isinstance(raw, HypothesisSet):
            return raw
        return HypothesisSet.model_validate(raw)

    async def revise_hypotheses(
        self,
        previous_hypotheses: HypothesisSet,
        new_context: IncidentContextSnapshot,
    ) -> HypothesisSet:
        method = "revise_hypotheses"
        call_num = self._increment_call(method)
        round_num = getattr(new_context, "round", call_num)

        raw = self._lookup(
            method=method,
            incident_id=previous_hypotheses.incident_id,
            round_num=round_num,
            call_num=call_num,
        )

        self._calls.append({
            "method": method,
            "incident_id": previous_hypotheses.incident_id,
            "round": round_num,
        })

        if isinstance(raw, HypothesisSet):
            return raw
        return HypothesisSet.model_validate(raw)

    # -----------------------------------------------------------------------
    # Helper lookup logic
    # -----------------------------------------------------------------------

    def _increment_call(self, method: str) -> int:
        count = self._call_counts.get(method, 0) + 1
        self._call_counts[method] = count
        return count

    def _lookup(
        self,
        method: str,
        incident_id: str,
        round_num: int,
        call_num: int,
    ) -> Any:
        # 1. Composite: incident:method:round
        key1 = f"{incident_id}:{method}:{round_num}"
        if key1 in self._recordings:
            return self._recordings[key1]

        # 2. Composite: incident:method:call_num
        key2 = f"{incident_id}:{method}:{call_num}"
        if key2 in self._recordings:
            return self._recordings[key2]

        # 3. Composite: incident:method
        key3 = f"{incident_id}:{method}"
        if key3 in self._recordings:
            return self._recordings[key3]

        # 4. Method with round: method:round
        key4 = f"{method}:{round_num}"
        if key4 in self._recordings:
            return self._recordings[key4]

        # 5. Method with call_num: method:call_num
        key5 = f"{method}:{call_num}"
        if key5 in self._recordings:
            return self._recordings[key5]

        # 6. Direct method key
        if method in self._recordings:
            val = self._recordings[method]
            if isinstance(val, list):
                idx = call_num - 1
                if idx < len(val):
                    return val[idx]
                raise UnrecordedRequestError(
                    f"Sequential queue for '{method}' exhausted (requested index {idx}, len {len(val)})."
                )
            return val

        raise UnrecordedRequestError(
            f"No recorded response found for method='{method}', "
            f"incident_id='{incident_id}', round={round_num}, call_num={call_num}. "
            f"Available recording keys: {list(self._recordings.keys())}"
        )
