"""The offline evaluation report: a per-assertion table, stored JSON and the two samples."""

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from finagent.eval.retrieval_eval import TOP_K, EvalReport, evaluate, load_case
from finagent.eval.scenarios import CaseResult, run_scenarios
from finagent.obs.redaction import redact
from finagent.orchestration.runner import ROOT
from finagent.rag.retrieve import default_retriever

OFFLINE_LABEL = "control flow, fake model"
SAMPLE_CASES = ("FIN-001", "FIN-002")


def retrieval_summary(report: EvalReport) -> dict[str, Any]:
    hits = [item for item in report.rules if item.rank is not None and item.rank <= TOP_K]
    return {
        "rule_source_recall_at_5": {"hits": len(hits), "total": len(report.rules)},
        "rule_misses": [item.rule_id for item in report.rules if item not in hits],
        "queries": [
            {"id": item.query_id, "hit": item.hit, "abstained": item.abstained} for item in report.queries
        ],
        "stale_in_authoritative": report.stale_in_authoritative,
        "non_authoritative_cited": report.non_authoritative_cited,
    }


def measure_retrieval(root: Path = ROOT) -> EvalReport:
    """Measure without writing, so an offline run never edits the Retrieval section."""
    rules = yaml.safe_load((root / "spec/policy_rules.yaml").read_text(encoding="utf-8"))["rules"]
    queries = yaml.safe_load((root / "tests/retrieval/golden.yaml").read_text(encoding="utf-8"))["queries"]
    case = load_case(root / "fixtures/cases/FIN-001")
    return evaluate(default_retriever(root), rules, queries, case)


def build_payload(
    cases: Sequence[CaseResult], retrieval: dict[str, Any], generated_at: datetime
) -> dict[str, Any]:
    return {
        "kind": "offline",
        "label": OFFLINE_LABEL,
        "generated_at": generated_at.isoformat(),
        "passed": bool(cases) and all(case.passed for case in cases),
        "cases": {
            case.case_id: {
                "passed": case.passed,
                "assertions": [
                    {
                        "key": item.key,
                        "expected": item.expected,
                        "actual": item.actual,
                        "passed": item.passed,
                    }
                    for item in case.assertions
                ],
                "artifacts": dict(case.artifacts),
            }
            for case in cases
        },
        "retrieval": retrieval,
    }


def render_table(payload: dict[str, Any]) -> str:
    lines = [f"Evaluation ({payload['label']})", "", "| Case | Assertion | Expected | Actual | Result |"]
    lines.append("|---|---|---|---|---|")
    for case_id, case in payload["cases"].items():
        for item in case["assertions"]:
            result = "pass" if item["passed"] else "FAIL"
            expected, actual = json.dumps(item["expected"]), json.dumps(item["actual"])
            lines.append(f"| {case_id} | {item['key']} | {expected} | {actual} | {result} |")
    total = sum(len(case["assertions"]) for case in payload["cases"].values())
    passed = sum(item["passed"] for case in payload["cases"].values() for item in case["assertions"])
    recall = payload["retrieval"].get("rule_source_recall_at_5", {})
    lines += [
        "",
        f"Assertions passed: {passed}/{total}",
        f"Rule-source recall@5: {recall.get('hits', 0)}/{recall.get('total', 0)}",
    ]
    return "\n".join(lines)


def render_sample(case_id: str, case: dict[str, Any], label: str) -> str:
    """A sample built only from the stored JSON, with sensitive data masked."""
    artifacts = redact(case["artifacts"])
    sections = [
        ("Request", artifacts["request"]),
        ("Key audit events", artifacts["key_audit_events"]),
        ("Final result", artifacts["final_result"]),
        ("Approval request", artifacts["approval_request"]),
    ]
    status = "passed" if case["passed"] else "FAILED"
    lines = [f"# {case_id} sample ({label})", "", f"Scenario assertions: {status}.", ""]
    for title, value in sections:
        body = json.dumps(value, indent=2, sort_keys=True)
        lines += [f"## {title}", "", "```json", body, "```", ""]
    return "\n".join(lines)


def write_samples(payload: dict[str, Any], directory: Path) -> list[Path]:
    written = []
    for case_id in SAMPLE_CASES:
        case = payload["cases"].get(case_id)
        if case is None or not case["artifacts"]:  # a crashed case has nothing truthful to show
            continue
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{case_id}.md"
        path.write_text(render_sample(case_id, case, payload["label"]), encoding="utf-8")
        written.append(path)
    return written


def store_payload(payload: dict[str, Any], home: Path, generated_at: datetime, prefix: str = "") -> Path:
    directory = Path(home) / "eval"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{prefix}{generated_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(redact(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def run_offline(home: Path, generated_at: datetime, root: Path = ROOT) -> tuple[dict[str, Any], Path]:
    payload = build_payload(run_scenarios(), retrieval_summary(measure_retrieval(root)), generated_at)
    path = store_payload(payload, home, generated_at)
    write_samples(json.loads(path.read_text(encoding="utf-8")), root / "samples")
    return payload, path


def list_runs(home: Path) -> list[dict[str, Any]]:
    runs = []
    for path in sorted((Path(home) / "eval").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            runs.append({"file": path.name, "error": "unreadable"})
            continue
        entry = {
            "file": path.name,
            "kind": data.get("kind"),
            "label": data.get("label"),
            "generated_at": data.get("generated_at"),
        }
        if data.get("kind") == "offline":
            entry["passed"] = data.get("passed")
            entry["cases"] = {key: value["passed"] for key, value in data.get("cases", {}).items()}
        else:
            entry["runs"] = [
                {key: row.get(key) for key in ("case_id", "config", "source")} for row in data.get("runs", [])
            ]
        runs.append(entry)
    return runs
