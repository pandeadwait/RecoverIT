"""
Deprecated compatibility shim for FakeReasoningProvider.

Per LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md §16.3 and §13.2,
scenario-specific fake reasoning has been moved to test support at
`tests.support.scripted_reasoning_provider`.

This module re-exports FakeReasoningProvider for backward compatibility
with existing test suites until legacy orchestration tests are migrated.
"""

from __future__ import annotations

from tests.support.scripted_reasoning_provider import (
    FakeReasoningProvider,
    ScriptedReasoningProvider,
)

__all__ = [
    "FakeReasoningProvider",
    "ScriptedReasoningProvider",
]
