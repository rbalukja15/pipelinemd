"""Catalog integrity. These guard every rule anyone adds later."""

from __future__ import annotations

import re

import pytest

from pipelinemd.models import Category, Confidence, Rule
from pipelinemd.rules import ALL_RULES, get_rule, rules_by_category


def test_ids_are_unique() -> None:
    ids = [rule.id for rule in ALL_RULES]
    duplicates = {name for name in ids if ids.count(name) > 1}
    assert not duplicates, f"duplicate rule ids: {sorted(duplicates)}"


def test_catalog_is_not_trivially_small() -> None:
    assert len(ALL_RULES) >= 50


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.id)
def test_rule_is_well_formed(rule: Rule) -> None:
    assert rule.id and "." in rule.id, "ids are namespaced, e.g. npm.eresolve"
    assert rule.title and not rule.title.endswith("."), "titles are labels, not sentences"
    assert isinstance(rule.category, Category)
    assert isinstance(rule.confidence, Confidence)
    assert rule.patterns, "a rule with no pattern can never fire"
    assert rule.explanation.strip(), "explain why it happens"
    assert rule.fixes, "a rule without a fix is only half a rule"
    assert all(fix.strip() for fix in rule.fixes)


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.id)
def test_patterns_compile(rule: Rule) -> None:
    for pattern in (*rule.patterns, *rule.excludes, *rule.requires):
        re.compile(pattern)


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.id)
def test_docs_look_like_urls(rule: Rule) -> None:
    assert all(doc.startswith("https://") for doc in rule.docs)


def test_every_category_has_at_least_one_rule() -> None:
    grouped = rules_by_category()
    missing = [category for category in Category if category not in grouped]
    assert not missing, f"categories with no rules: {missing}"


def test_get_rule_round_trips() -> None:
    assert get_rule("npm.eresolve") is not None
    assert get_rule("no.such.rule") is None
