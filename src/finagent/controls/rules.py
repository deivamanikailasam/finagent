"""Typed access to the signed-off rules in spec/policy_rules.yaml."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RULES_PATH = Path(__file__).resolve().parents[3] / "spec" / "policy_rules.yaml"


@dataclass(frozen=True)
class Rule:
    id: str
    citations: tuple[str, ...]
    outcome_on_fail: str
    category: Any
    owner: str
    owner_basis: str
    threshold: dict[str, Any]


def _citations(source: dict[str, Any]) -> tuple[str, ...]:
    extra = [
        f"{item.get('doc', source['doc'])}#{item['section']}"
        for item in source.get("additional_sections", [])
    ]
    return (f"{source['doc']}#{source['section']}", *extra)


@lru_cache(maxsize=1)
def load_rules() -> dict[str, Rule]:
    with RULES_PATH.open(encoding="utf-8") as handle:
        spec = yaml.safe_load(handle)
    return {
        item["id"]: Rule(
            id=item["id"],
            citations=_citations(item["source"]),
            outcome_on_fail=item["outcome_on_fail"],
            category=item["category"],
            owner=item["owner"]["who"],
            owner_basis=item["owner"]["basis"],
            threshold=item["threshold"] or {},
        )
        for item in spec["rules"]
    }


def rule(rule_id: str) -> Rule:
    return load_rules()[rule_id]
