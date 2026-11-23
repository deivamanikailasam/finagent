"""WP 8.1: the run service and CLI, against the real graph with the fake model."""

import json
import shutil
from pathlib import Path

import anthropic
import pytest
from typer.testing import CliRunner

from finagent.approvals.service import ApprovalService
from finagent.cli.main import app
from finagent.orchestration.runner import Runner

ROOT = Path(__file__).resolve().parents[2]
FIN_001 = str(ROOT / "fixtures/cases/FIN-001")
FIN_004 = str(ROOT / "fixtures/cases/FIN-004")
GET_KEYS = {"run_id", "status", "current_node", "result", "audit_events"}
APPROVER = "cc-manager-01"


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("FINAGENT_HOME", str(tmp_path))
    runner = CliRunner()

    def invoke(*args):
        return runner.invoke(app, list(args))

    return invoke


def out(result):
    return json.loads(result.stdout)


def start(cli, case=FIN_001):
    result = cli("run", "start", "--case", case, "--json")
    assert result.exit_code == 0, result.output
    return out(result)


def request_file(tmp_path, **changes):
    data = json.loads((ROOT / "fixtures/cases/FIN-001/request.json").read_text(encoding="utf-8"))
    path = tmp_path / "request.json"
    path.write_text(json.dumps({**data, **changes}), encoding="utf-8")
    return str(path)


def test_start_get_approve_continue_the_same_run(cli):
    started = start(cli)
    assert started["status"] == "AWAITING_APPROVAL"
    fetched = out(cli("run", "get", started["run_id"], "--json"))
    assert fetched["run_id"] == started["run_id"]
    approved = cli("run", "approve", started["run_id"], "--approver", APPROVER, "--json")
    assert approved.exit_code == 0, approved.output
    body = out(approved)
    assert (body["run_id"], body["status"], body["code"]) == (started["run_id"], "COMPLETE", "APPROVED")
    assert [item["action"] for item in body["result"]["actions_taken"]] == ["approval_request", "POST"]


def test_a_hold_commits_through_the_real_thread_executor(cli):
    started = start(cli, FIN_004)
    body = out(cli("run", "approve", started["run_id"], "--approver", APPROVER, "--json"))
    assert body["status"] == "COMPLETE"
    assert [item["action"] for item in body["result"]["actions_taken"]] == [
        "BLOCK",
        "approval_request",
        "HOLD",
    ]


def test_start_reject_continue_the_same_run(cli):
    started = start(cli)
    rejected = cli(
        "run", "reject", started["run_id"], "--approver", APPROVER, "--reason", "not ours", "--json"
    )
    assert rejected.exit_code == 0, rejected.output
    body = out(rejected)
    assert (body["run_id"], body["status"]) == (started["run_id"], "REJECTED_BY_APPROVER")


def test_run_get_keys_for_paused_and_completed_runs(cli):
    started = start(cli)
    assert set(out(cli("run", "get", started["run_id"], "--json"))) == GET_KEYS
    cli("run", "approve", started["run_id"], "--approver", APPROVER, "--json")
    completed = out(cli("run", "get", started["run_id"], "--json"))
    assert set(completed) == GET_KEYS
    assert completed["status"] == "COMPLETE"
    assert completed["result"]["recommendation"]["outcome"] == "APPROVE_FOR_POSTING"
    assert completed["audit_events"]


def test_no_audit_omits_the_events(cli):
    started = start(cli)
    body = out(cli("run", "get", started["run_id"], "--no-audit", "--json"))
    assert set(body) == GET_KEYS - {"audit_events"}


def test_repeating_an_approval_gives_the_identical_response_and_one_post(cli):
    started = start(cli)
    first = cli("run", "approve", started["run_id"], "--approver", APPROVER, "--json")
    second = cli("run", "approve", started["run_id"], "--approver", APPROVER, "--json")
    assert first.stdout == second.stdout
    actions = [item["action"] for item in out(second)["result"]["actions_taken"]]
    assert actions.count("POST") == 1


def test_ineligible_approver_exits_nonzero_and_the_run_stays_paused(cli):
    started = start(cli)
    result = cli("run", "approve", started["run_id"], "--approver", "nobody", "--json")
    assert result.exit_code == 1
    body = out(result)
    assert (body["applied"], body["status"]) == (False, "AWAITING_APPROVAL")


def test_a_run_attached_to_another_runs_request_cannot_decide_it(cli):
    first, second = start(cli), start(cli)
    assert first["pending_approval_id"] == second["pending_approval_id"]
    result = cli("run", "approve", second["run_id"], "--approver", APPROVER, "--json")
    assert result.exit_code == 2
    assert out(cli("run", "get", first["run_id"], "--json"))["status"] == "AWAITING_APPROVAL"


def replaced_request(cli, tmp_path):
    """Approve a POST whose vendor is then blocked, so revalidation creates a new request."""
    case = tmp_path / "cases" / "FIN-001"
    shutil.copytree(FIN_001, case)
    started = start(cli, str(case))
    vendor_file = case / "vendor.json"
    vendor = json.loads(vendor_file.read_text(encoding="utf-8"))
    vendor_file.write_text(json.dumps({**vendor, "status": "BLOCKED"}), encoding="utf-8")
    first = out(cli("run", "approve", started["run_id"], "--approver", APPROVER, "--json"))
    assert first["status"] == "AWAITING_APPROVAL"
    assert first["pending_approval_id"] != first["approval_id"]
    return started["run_id"], first["pending_approval_id"]


@pytest.mark.parametrize("command", ["approve", "reject"])
def test_a_repeated_command_cannot_decide_a_replacement_request(cli, tmp_path, command):
    run_id, pending = replaced_request(cli, tmp_path)
    extra = ("--reason", "not ours") if command == "reject" else ()
    result = cli("run", command, run_id, "--approver", APPROVER, *extra, "--json")
    assert result.exit_code == 2
    assert pending in result.stderr
    body = out(cli("run", "get", run_id, "--json"))
    assert body["status"] == "AWAITING_APPROVAL"
    assert not [item for item in body["result"]["actions_taken"] if item["action"] in {"POST", "HOLD"}]


def test_a_replacement_request_can_be_decided_by_its_id(cli, tmp_path):
    run_id, pending = replaced_request(cli, tmp_path)
    result = cli("run", "approve", run_id, "--approver", APPROVER, "--approval-id", pending, "--json")
    assert result.exit_code == 0, result.output
    assert out(result)["status"] == "COMPLETE"


def test_a_stale_approval_id_is_refused(cli, tmp_path):
    run_id, _ = replaced_request(cli, tmp_path)
    result = cli("run", "approve", run_id, "--approver", APPROVER, "--approval-id", "old", "--json")
    assert result.exit_code == 2


def test_request_file_starts_a_run(cli, tmp_path):
    result = cli("run", "start", "--request", request_file(tmp_path), "--json")
    assert result.exit_code == 0, result.output
    assert out(result)["status"] == "AWAITING_APPROVAL"


@pytest.mark.parametrize("amount", [9900.0, "0", "0.00"])
def test_request_with_a_float_or_zero_amount_is_rejected(cli, tmp_path, amount):
    result = cli("run", "start", "--request", request_file(tmp_path, amount=amount), "--json")
    assert result.exit_code == 2
    assert result.stdout == ""
    if amount != 9900.0:
        assert "UNSUPPORTED_DOCUMENT_TYPE" in result.stderr


def test_request_cannot_point_outside_the_cases_directory(cli, tmp_path):
    result = cli("run", "start", "--request", request_file(tmp_path, case_id="../../etc"), "--json")
    assert result.exit_code == 2


def test_start_needs_exactly_one_source(cli, tmp_path):
    assert cli("run", "start", "--json").exit_code == 2
    both = cli("run", "start", "--case", FIN_001, "--request", request_file(tmp_path), "--json")
    assert both.exit_code == 2


def test_as_of_needs_a_utc_offset(cli):
    result = cli("run", "start", "--case", FIN_001, "--as-of", "2026-09-15T09:00:00", "--json")
    assert result.exit_code == 2


def test_unknown_run_is_a_clean_error(cli):
    result = cli("run", "get", "missing", "--json")
    assert result.exit_code == 2
    assert "unknown run" in result.stderr


def test_resume_leaves_a_paused_run_paused(cli):
    started = start(cli, FIN_004)
    body = out(cli("run", "resume", started["run_id"], "--json"))
    assert (body["status"], body["pending_approval_id"]) == (
        "AWAITING_APPROVAL",
        started["pending_approval_id"],
    )


def test_eval_list_is_empty_before_any_run(cli):
    assert out(cli("eval", "list", "--json")) == {"runs": []}


def test_ingest_records_the_manifest(cli, tmp_path):
    result = cli("ingest")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "index_manifest.json").is_file()


@pytest.mark.parametrize("command", ["approve", "reject"])
def test_decisions_call_only_decide_then_resume(cli, monkeypatch, command):
    started = start(cli)
    calls = []
    real_decide, real_resume = ApprovalService.decide, Runner.resume

    def decide(self, *args, **kwargs):
        calls.append("decide")
        return real_decide(self, *args, **kwargs)

    def resume(self, run_id):
        calls.append(("resume", run_id))
        return real_resume(self, run_id)

    monkeypatch.setattr(ApprovalService, "decide", decide)
    monkeypatch.setattr(Runner, "resume", resume)
    extra = ("--reason", "not ours") if command == "reject" else ()
    cli("run", command, started["run_id"], "--approver", APPROVER, *extra, "--json")
    assert calls == ["decide", ("resume", started["run_id"])]


def test_the_cli_never_builds_a_real_client_even_with_a_key(cli, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")

    def refuse(*_args, **_kwargs):
        raise AssertionError("the fake model is the default")

    monkeypatch.setattr(anthropic, "Anthropic", refuse)
    assert start(cli)["status"] == "AWAITING_APPROVAL"


def test_runtime_code_imports_nothing_from_scripts_fixtures_or_tests():
    for path in (ROOT / "src").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("from tests", "import tests", "from fixtures", "import fixtures"))
            assert not stripped.startswith(("from scripts", "import scripts"))
