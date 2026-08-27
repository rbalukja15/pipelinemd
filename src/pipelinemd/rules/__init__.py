"""Deterministic failure signatures and the engine that applies them."""

from .catalog import ALL_RULES, get_rule, rules_by_category
from .engine import match_rules

__all__ = ["ALL_RULES", "get_rule", "match_rules", "rules_by_category"]
