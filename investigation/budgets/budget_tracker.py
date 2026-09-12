"""
Investigation budget tracking and limit enforcement.

Tracks rounds, queries, reasoning calls, units (tokens), and elapsed time.
Enforces limits specified in InvestigationBudget and generates the final
BudgetUsage contract object.

See WORK_DIVISION.md §8.4 and ARCHITECTURE.md §9.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from contracts.errors.schemas import BUDGET_EXHAUSTED, StructuredError
from contracts.hypothesis.schemas import BudgetUsage
from contracts.investigation.schemas import InvestigationBudget


@dataclass(frozen=True)
class BudgetCost:
    """Estimated cost of an operation for affordability checks."""
    rounds: int = 0
    queries: int = 0
    reasoning_calls: int = 0
    input_units: int = 0
    output_units: int = 0
    elapsed_seconds: float = 0.0


class BudgetExhaustedError(Exception):
    """Raised when an operation would exceed or has exceeded the investigation budget."""

    def __init__(self, error: StructuredError) -> None:
        super().__init__(error.message)
        self.error = error


class BudgetTracker:
    """
    Monitors and enforces resource consumption against an InvestigationBudget.

    Guarantees:
    - Tracks rounds used, queries issued, elapsed time, reasoning calls, and input/output units.
    - Exposes is_budget_exhausted(), remaining(), and can_afford(estimated_cost).
    - Enforces all limits from InvestigationBudget.
    - Produces canonical BudgetUsage contract instances.
    """

    def __init__(
        self,
        budget: InvestigationBudget,
        clock: Callable[[], float] | None = None,
        start_time: float | None = None,
    ) -> None:
        self._budget = budget
        self._clock = clock or time.time
        self._start_time = start_time if start_time is not None else self._clock()

        self._rounds: int = 0
        self._queries: int = 0
        self._reasoning_calls: int = 0
        self._input_units: int = 0
        self._output_units: int = 0
        self._manual_elapsed_seconds: float | None = None

    @property
    def budget(self) -> InvestigationBudget:
        """The configured budget limits."""
        return self._budget

    @property
    def rounds(self) -> int:
        """Number of evidence-gathering rounds executed."""
        return self._rounds

    @property
    def queries(self) -> int:
        """Total evidence queries issued."""
        return self._queries

    @property
    def reasoning_calls(self) -> int:
        """Total calls made to the reasoning provider."""
        return self._reasoning_calls

    @property
    def input_units(self) -> int:
        """Total provider-neutral input units (e.g. tokens) consumed."""
        return self._input_units

    @property
    def output_units(self) -> int:
        """Total provider-neutral output units (e.g. tokens) consumed."""
        return self._output_units

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed investigation time in seconds."""
        if self._manual_elapsed_seconds is not None:
            return self._manual_elapsed_seconds
        return max(0.0, self._clock() - self._start_time)

    # -----------------------------------------------------------------------
    # Recording operations
    # -----------------------------------------------------------------------

    def record_round(self, count: int = 1, enforce: bool = False) -> None:
        """Record the start/completion of an investigation round."""
        if enforce and not self.can_afford({"rounds": count}):
            raise BudgetExhaustedError(
                self._create_exhausted_error(
                    f"Recording {count} round(s) would exceed max_rounds ({self._budget.max_rounds})."
                )
            )
        self._rounds += count

    def record_queries(self, count: int = 1, enforce: bool = False) -> None:
        """Record evidence queries issued."""
        if enforce and not self.can_afford({"queries": count}):
            raise BudgetExhaustedError(
                self._create_exhausted_error(
                    f"Recording {count} querie(s) would exceed max_queries ({self._budget.max_queries})."
                )
            )
        self._queries += count

    def record_reasoning_call(
        self,
        input_units: int = 0,
        output_units: int = 0,
        enforce: bool = False,
    ) -> None:
        """Record a call to the reasoning provider with units consumed."""
        cost = {
            "reasoning_calls": 1,
            "input_units": input_units,
            "output_units": output_units,
        }
        if enforce and not self.can_afford(cost):
            raise BudgetExhaustedError(
                self._create_exhausted_error(
                    "Reasoning call would exceed configured budget limits."
                )
            )
        self._reasoning_calls += 1
        self._input_units += input_units
        self._output_units += output_units

    def record_units(
        self,
        input_units: int = 0,
        output_units: int = 0,
        enforce: bool = False,
    ) -> None:
        """Record units consumed without incrementing reasoning_calls."""
        cost = {
            "input_units": input_units,
            "output_units": output_units,
        }
        if enforce and not self.can_afford(cost):
            raise BudgetExhaustedError(
                self._create_exhausted_error(
                    "Unit consumption would exceed configured budget limits."
                )
            )
        self._input_units += input_units
        self._output_units += output_units

    def advance_time(self, seconds: float) -> None:
        """Advance manual or simulated elapsed time by seconds."""
        if self._manual_elapsed_seconds is None:
            self._manual_elapsed_seconds = self.elapsed_seconds + seconds
        else:
            self._manual_elapsed_seconds += seconds

    def set_elapsed_seconds(self, seconds: float) -> None:
        """Set manual elapsed seconds for testing or replay."""
        self._manual_elapsed_seconds = max(0.0, seconds)

    # -----------------------------------------------------------------------
    # Limit queries & affordability
    # -----------------------------------------------------------------------

    def is_budget_exhausted(self) -> bool:
        """
        Return True if any configured budget limit has been reached or exceeded.
        """
        return len(self.get_exhausted_limits()) > 0

    def get_exhausted_limits(self) -> list[str]:
        """Return the names of all limits that have been reached or exceeded."""
        exhausted: list[str] = []
        if self._rounds >= self._budget.max_rounds:
            exhausted.append("rounds")
        if self._queries >= self._budget.max_queries:
            exhausted.append("queries")
        if self.elapsed_seconds >= self._budget.max_elapsed_seconds:
            exhausted.append("elapsed_seconds")
        if self._reasoning_calls >= self._budget.max_reasoning_calls:
            exhausted.append("reasoning_calls")
        if self._input_units >= self._budget.max_input_units:
            exhausted.append("input_units")
        if self._output_units >= self._budget.max_output_units:
            exhausted.append("output_units")
        return exhausted

    def remaining(self) -> dict[str, int | float]:
        """
        Return remaining capacity for each tracked resource.
        Values are clamped to 0 (never negative).
        """
        return {
            "rounds": max(0, self._budget.max_rounds - self._rounds),
            "queries": max(0, self._budget.max_queries - self._queries),
            "elapsed_seconds": max(
                0.0, float(self._budget.max_elapsed_seconds - self.elapsed_seconds)
            ),
            "reasoning_calls": max(
                0, self._budget.max_reasoning_calls - self._reasoning_calls
            ),
            "input_units": max(0, self._budget.max_input_units - self._input_units),
            "output_units": max(0, self._budget.max_output_units - self._output_units),
        }

    def can_afford(self, estimated_cost: dict[str, Any] | BudgetCost) -> bool:
        """
        Check whether an estimated operation can fit within the remaining budget.
        """
        rem = self.remaining()

        cost_dict: dict[str, Any]
        if isinstance(estimated_cost, BudgetCost):
            cost_dict = {
                "rounds": estimated_cost.rounds,
                "queries": estimated_cost.queries,
                "reasoning_calls": estimated_cost.reasoning_calls,
                "input_units": estimated_cost.input_units,
                "output_units": estimated_cost.output_units,
                "elapsed_seconds": estimated_cost.elapsed_seconds,
            }
        else:
            cost_dict = estimated_cost

        for resource, required in cost_dict.items():
            if resource in rem:
                if required > rem[resource]:
                    return False
        return True

    def check_affordable(self, estimated_cost: dict[str, Any] | BudgetCost) -> None:
        """
        Raise BudgetExhaustedError if the estimated cost cannot be afforded.
        """
        if not self.can_afford(estimated_cost):
            raise BudgetExhaustedError(
                self._create_exhausted_error(
                    f"Estimated cost {estimated_cost} exceeds remaining budget {self.remaining()}."
                )
            )

    # -----------------------------------------------------------------------
    # Output summaries
    # -----------------------------------------------------------------------

    def get_budget_usage(self) -> BudgetUsage:
        """
        Return a BudgetUsage contract object suitable for RankedHypothesisSet.
        """
        return BudgetUsage(
            rounds=self._rounds,
            queries=self._queries,
            reasoning_calls=self._reasoning_calls,
        )

    def get_detailed_usage(self) -> dict[str, Any]:
        """Return a detailed dictionary of all usage, limits, and remaining amounts."""
        return {
            "used": {
                "rounds": self._rounds,
                "queries": self._queries,
                "reasoning_calls": self._reasoning_calls,
                "input_units": self._input_units,
                "output_units": self._output_units,
                "elapsed_seconds": self.elapsed_seconds,
            },
            "limits": {
                "max_rounds": self._budget.max_rounds,
                "max_queries": self._budget.max_queries,
                "max_reasoning_calls": self._budget.max_reasoning_calls,
                "max_input_units": self._budget.max_input_units,
                "max_output_units": self._budget.max_output_units,
                "max_elapsed_seconds": self._budget.max_elapsed_seconds,
            },
            "remaining": self.remaining(),
            "exhausted_limits": self.get_exhausted_limits(),
            "is_exhausted": self.is_budget_exhausted(),
        }

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _create_exhausted_error(self, message: str) -> StructuredError:
        return StructuredError(
            code=BUDGET_EXHAUSTED,
            message=message,
            retryable=False,
            source="investigation.budgets.budget_tracker",
            details={
                "exhausted_limits": self.get_exhausted_limits(),
                "used": {
                    "rounds": self._rounds,
                    "queries": self._queries,
                    "reasoning_calls": self._reasoning_calls,
                    "input_units": self._input_units,
                    "output_units": self._output_units,
                    "elapsed_seconds": self.elapsed_seconds,
                },
                "limits": {
                    "max_rounds": self._budget.max_rounds,
                    "max_queries": self._budget.max_queries,
                    "max_reasoning_calls": self._budget.max_reasoning_calls,
                    "max_input_units": self._budget.max_input_units,
                    "max_output_units": self._budget.max_output_units,
                    "max_elapsed_seconds": self._budget.max_elapsed_seconds,
                },
            },
        )
