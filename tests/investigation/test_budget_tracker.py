"""
Unit tests for BudgetTracker.

Verifies:
- Tracking rounds, queries, reasoning calls, input/output units, and elapsed time.
- Budget tracker correctly detects exhaustion for EACH limit type.
- Affordability calculation (can_afford and check_affordable).
- Enforcement raises BudgetExhaustedError with code BUDGET_EXHAUSTED.
- Remaining capacity is clamped to zero and correctly computed.
- Generates canonical BudgetUsage contract instance for RankedHypothesisSet.

See WORK_DIVISION.md §8.4 and ARCHITECTURE.md §9.
"""

from __future__ import annotations

import pytest

from contracts.errors.schemas import BUDGET_EXHAUSTED
from contracts.hypothesis.schemas import BudgetUsage
from contracts.investigation.schemas import InvestigationBudget
from investigation.budgets.budget_tracker import (
    BudgetCost,
    BudgetExhaustedError,
    BudgetTracker,
)


@pytest.fixture
def test_budget() -> InvestigationBudget:
    return InvestigationBudget(
        max_rounds=3,
        max_queries=6,
        max_elapsed_seconds=300,
        max_reasoning_calls=5,
        max_input_units=10_000,
        max_output_units=2_000,
        minimum_hypotheses=2,
        maximum_hypotheses=5,
    )


# ---------------------------------------------------------------------------
# Initial Tracking State
# ---------------------------------------------------------------------------


def test_initial_tracker_state(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)

    assert tracker.rounds == 0
    assert tracker.queries == 0
    assert tracker.reasoning_calls == 0
    assert tracker.input_units == 0
    assert tracker.output_units == 0
    assert not tracker.is_budget_exhausted()
    assert tracker.get_exhausted_limits() == []

    rem = tracker.remaining()
    assert rem["rounds"] == 3
    assert rem["queries"] == 6
    assert rem["reasoning_calls"] == 5
    assert rem["input_units"] == 10_000
    assert rem["output_units"] == 2_000
    assert rem["elapsed_seconds"] <= 300.0


# ---------------------------------------------------------------------------
# Exhaustion Tests for Each Individual Limit Type
# ---------------------------------------------------------------------------


def test_exhaustion_on_max_rounds(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)

    tracker.record_round(2)
    assert not tracker.is_budget_exhausted()
    assert tracker.remaining()["rounds"] == 1

    tracker.record_round(1)
    assert tracker.is_budget_exhausted()
    assert "rounds" in tracker.get_exhausted_limits()
    assert tracker.remaining()["rounds"] == 0


def test_exhaustion_on_max_queries(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)

    tracker.record_queries(5)
    assert not tracker.is_budget_exhausted()
    assert tracker.remaining()["queries"] == 1

    tracker.record_queries(1)
    assert tracker.is_budget_exhausted()
    assert "queries" in tracker.get_exhausted_limits()
    assert tracker.remaining()["queries"] == 0


def test_exhaustion_on_max_elapsed_seconds(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)

    tracker.set_elapsed_seconds(299.0)
    assert not tracker.is_budget_exhausted()
    assert tracker.remaining()["elapsed_seconds"] == pytest.approx(1.0)

    tracker.set_elapsed_seconds(300.0)
    assert tracker.is_budget_exhausted()
    assert "elapsed_seconds" in tracker.get_exhausted_limits()
    assert tracker.remaining()["elapsed_seconds"] == 0.0

    tracker.set_elapsed_seconds(350.0)
    assert tracker.is_budget_exhausted()
    assert tracker.remaining()["elapsed_seconds"] == 0.0


def test_exhaustion_on_max_reasoning_calls(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)

    for _ in range(4):
        tracker.record_reasoning_call(input_units=100, output_units=50)

    assert not tracker.is_budget_exhausted()
    assert tracker.remaining()["reasoning_calls"] == 1

    tracker.record_reasoning_call(input_units=100, output_units=50)
    assert tracker.is_budget_exhausted()
    assert "reasoning_calls" in tracker.get_exhausted_limits()
    assert tracker.remaining()["reasoning_calls"] == 0


def test_exhaustion_on_max_input_units(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)

    tracker.record_units(input_units=9_999, output_units=100)
    assert not tracker.is_budget_exhausted()

    tracker.record_units(input_units=1, output_units=0)
    assert tracker.is_budget_exhausted()
    assert "input_units" in tracker.get_exhausted_limits()
    assert tracker.remaining()["input_units"] == 0


def test_exhaustion_on_max_output_units(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)

    tracker.record_units(input_units=100, output_units=1_999)
    assert not tracker.is_budget_exhausted()

    tracker.record_units(input_units=0, output_units=1)
    assert tracker.is_budget_exhausted()
    assert "output_units" in tracker.get_exhausted_limits()
    assert tracker.remaining()["output_units"] == 0


def test_multiple_limits_exhausted_simultaneously(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)

    tracker.record_round(3)
    tracker.record_queries(6)
    tracker.set_elapsed_seconds(305.0)

    exhausted = tracker.get_exhausted_limits()
    assert "rounds" in exhausted
    assert "queries" in exhausted
    assert "elapsed_seconds" in exhausted


# ---------------------------------------------------------------------------
# Affordability and Pre-Check Tests
# ---------------------------------------------------------------------------


def test_can_afford_with_dict(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)
    tracker.record_queries(4)  # remaining = 2

    assert tracker.can_afford({"queries": 2})
    assert tracker.can_afford({"queries": 1})
    assert not tracker.can_afford({"queries": 3})


def test_can_afford_with_budget_cost(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)
    tracker.record_round(2)  # remaining rounds = 1

    affordable_cost = BudgetCost(rounds=1, queries=3, reasoning_calls=2)
    assert tracker.can_afford(affordable_cost)

    too_expensive = BudgetCost(rounds=2, queries=3)
    assert not tracker.can_afford(too_expensive)


def test_check_affordable_raises_on_unaffordable(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)
    tracker.record_queries(5)

    # 2 more queries would exceed 6
    with pytest.raises(BudgetExhaustedError) as exc_info:
        tracker.check_affordable({"queries": 2})

    assert exc_info.value.error.code == BUDGET_EXHAUSTED
    assert exc_info.value.error.source == "investigation.budgets.budget_tracker"


# ---------------------------------------------------------------------------
# Enforcement on Recording
# ---------------------------------------------------------------------------


def test_record_queries_enforce(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)
    tracker.record_queries(5)

    with pytest.raises(BudgetExhaustedError) as exc_info:
        tracker.record_queries(2, enforce=True)

    assert exc_info.value.error.code == BUDGET_EXHAUSTED
    assert tracker.queries == 5  # wasn't updated


def test_record_round_enforce(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)
    tracker.record_round(3)

    with pytest.raises(BudgetExhaustedError) as exc_info:
        tracker.record_round(1, enforce=True)

    assert exc_info.value.error.code == BUDGET_EXHAUSTED
    assert tracker.rounds == 3


def test_record_reasoning_call_enforce(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)
    for _ in range(5):
        tracker.record_reasoning_call()

    with pytest.raises(BudgetExhaustedError) as exc_info:
        tracker.record_reasoning_call(enforce=True)

    assert exc_info.value.error.code == BUDGET_EXHAUSTED
    assert tracker.reasoning_calls == 5


# ---------------------------------------------------------------------------
# Summary Output Tests
# ---------------------------------------------------------------------------


def test_get_budget_usage_produces_contract_model(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)
    tracker.record_round(2)
    tracker.record_queries(5)
    tracker.record_reasoning_call(input_units=500, output_units=200)

    usage = tracker.get_budget_usage()
    assert isinstance(usage, BudgetUsage)
    assert usage.rounds == 2
    assert usage.queries == 5
    assert usage.reasoning_calls == 1

    # Round-trip JSON validation
    json_str = usage.model_dump_json()
    reloaded = BudgetUsage.model_validate_json(json_str)
    assert reloaded == usage


def test_get_detailed_usage(test_budget: InvestigationBudget) -> None:
    tracker = BudgetTracker(budget=test_budget)
    tracker.record_round(1)
    tracker.record_queries(3)
    tracker.record_reasoning_call(input_units=1000, output_units=200)
    tracker.set_elapsed_seconds(45.0)

    detailed = tracker.get_detailed_usage()
    assert detailed["used"]["rounds"] == 1
    assert detailed["used"]["queries"] == 3
    assert detailed["used"]["reasoning_calls"] == 1
    assert detailed["used"]["input_units"] == 1000
    assert detailed["used"]["output_units"] == 200
    assert detailed["used"]["elapsed_seconds"] == 45.0
    assert detailed["remaining"]["rounds"] == 2
    assert detailed["remaining"]["queries"] == 3
    assert not detailed["is_exhausted"]
