"""
Reasoning package.

Contains the ReasoningProvider abstraction, hypothesis generation/revision,
citation validation, stopping evaluation, and the deterministic ranking engine.
"""

from reasoning.hypotheses.service import DefaultHypothesisService
from reasoning.provider.interface import ReasoningProvider
from reasoning.ranking.ranking_engine import RankingEngine
from reasoning.stopping import DefaultStoppingService, StoppingRuleEvaluator

__all__ = [
    "DefaultHypothesisService",
    "DefaultStoppingService",
    "RankingEngine",
    "ReasoningProvider",
    "StoppingRuleEvaluator",
]

