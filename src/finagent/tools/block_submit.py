"""The only two write tools: a restrict-only payment block and a human-approved decision."""

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml

from finagent.config.clock import Clock
from finagent.domain.enums import AuthorisationType
from finagent.obs.log import log_event
from finagent.persistence.db import transaction
from finagent.tools.schemas import (
    PlacePaymentBlockInput,
    SubmitFinanceDecisionInput,
    WriteOutput,
    payload_hash_of,
)

PERMISSIONS_PATH = Path(__file__).resolve().parents[3] / "spec" / "submit_permissions.yaml"
Audit = Callable[[dict[str, Any]], None]


class WriteRefused(Exception):
    """A definite answer from a write tool. It is never an unknown result and never retried."""


class Denied(WriteRefused):
    """The permission check failed. Nothing was written."""


class IdempotencyConflict(WriteRefused):
    """The same idempotency key was used with a different payload."""


class DuplicateRisk(WriteRefused):
    """The invoice already has a POST row."""


@dataclass(frozen=True)
class ApprovalView:
    approval_id: str
    run_id: str
    payload_hash: str
    action: str
    status: str


class ApprovalLookup(Protocol):
    def get(self, approval_id: str) -> ApprovalView | None: ...


@lru_cache(maxsize=1)
def _permissions() -> dict[str, Any]:
    with PERMISSIONS_PATH.open(encoding="utf-8") as handle:
        spec = yaml.safe_load(handle)
    if spec.get("default") != "DENY":
        raise ValueError("submit permissions must default to DENY")
    return spec


def derive_key(run_id: str, approval_or_authorisation_id: str, action: str) -> str:
    return hashlib.sha256(f"{run_id}|{approval_or_authorisation_id}|{action}".encode()).hexdigest()


def block_authorisation_id(run_id: str, rule_id: str) -> str:
    return hashlib.sha256(f"{run_id}|{rule_id}|BLOCK".encode()).hexdigest()


def _invoice_key(vendor_id: str, invoice_no_norm: str) -> str:
    return f"{vendor_id}|{invoice_no_norm}"


def _after_ledger_insert(conn: sqlite3.Connection) -> None:
    """Failure-injection point between the ledger write and the idempotency record."""


def _emit(audit: Audit | None, event: dict[str, Any]) -> None:
    if audit is None:
        return
    try:
        audit(event)
    except Exception:  # the write is already committed, so an audit fault must not report it as failed
        log_event("audit_write_failed", outcome="error", tool=str(event.get("tool")))


def _event(
    tool: str, name: str, run_id: str, action: str, outcome: str, clock: Clock, started: float, **extra: Any
) -> dict[str, Any]:
    return {
        "ts": clock.now_utc().isoformat(),
        "run_id": run_id,
        "event": name,
        "tool": tool,
        "action": action,
        "outcome": outcome,
        "duration_ms": (time.perf_counter() - started) * 1000,
        **extra,
    }


def _block_output(authorisation_id: str) -> WriteOutput:
    return WriteOutput(status="BLOCKED", posting_ref=f"BLOCK-{authorisation_id[:16]}")


def place_payment_block(
    conn: sqlite3.Connection, request: PlacePaymentBlockInput, clock: Clock, audit: Audit | None = None
) -> WriteOutput:
    """Place a block, or return the run's existing block. It can only restrict payment."""
    started = time.perf_counter()
    rule = _permissions()["tools"]["place_payment_block"]["actions"].get(request.action)
    if rule is None or request.authorisation.type != rule["requires"]:
        raise Denied("block requires action BLOCK under SYSTEM_FAILSAFE")
    authorisation_id = block_authorisation_id(request.run_id, request.rule_id)
    if request.authorisation.id != authorisation_id:
        raise Denied("authorisation is not bound to this run and rule")
    if request.idempotency_key != derive_key(request.run_id, authorisation_id, request.action):
        raise Denied("idempotency key does not match")
    if request.payload_hash != payload_hash_of(request.payload):
        raise Denied("payload hash does not match")
    invoice_key = _invoice_key(request.payload.vendor_id, request.payload.invoice_no_norm)
    with transaction(conn):
        existing = conn.execute(
            "SELECT authorisation_id FROM blocks WHERE run_id = ? AND action = 'BLOCK'", (request.run_id,)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO blocks (run_id, action, rule_id, invoice_key, authorisation_id, created_at) "
                "VALUES (?, 'BLOCK', ?, ?, ?, ?)",
                (request.run_id, request.rule_id, invoice_key, authorisation_id, clock.now_utc().isoformat()),
            )
    output = _block_output(existing[0] if existing else authorisation_id)
    outcome = "existing_block" if existing else "committed"
    _emit(
        audit,
        _event(
            "place_payment_block",
            "block_placed",
            request.run_id,
            "BLOCK",
            outcome,
            clock,
            started,
            rule_id=request.rule_id,
            posting_ref=output.posting_ref,
        ),
    )
    return output


def _check_submit_permission(
    request: SubmitFinanceDecisionInput, approvals: ApprovalLookup
) -> dict[str, Any]:
    """Deny by default: every condition in the spec must hold."""
    rule = _permissions()["tools"]["submit_finance_decision"]["actions"].get(request.action)
    if rule is None:
        raise Denied("action is not permitted")
    if request.authorisation.type != AuthorisationType(rule["requires"]):
        raise Denied("action requires a human approval")
    if request.payload_hash != payload_hash_of(request.payload):
        raise Denied("payload hash does not match")
    approval = approvals.get(request.authorisation.id)
    if approval is None or approval.approval_id != request.authorisation.id:
        raise Denied("no approval record")
    if approval.status != rule["approval_state"] or approval.status in rule["never_state"]:
        raise Denied("approval is not in an approved state")
    for field in rule["bound_to"]:
        if getattr(approval, field) != getattr(request, field):
            raise Denied(f"approval is not bound to this {field}")
    if request.idempotency_key != derive_key(request.run_id, approval.approval_id, request.action):
        raise Denied("idempotency key does not match")
    return rule


def _insert_decision(conn: sqlite3.Connection, request: SubmitFinanceDecisionInput, now: str) -> WriteOutput:
    payload = request.payload
    amount = str(payload.amount)  # canonical two-decimal text
    posting_ref = f"{request.action}-{request.idempotency_key[:16]}"
    cursor = conn.execute(
        "INSERT INTO ledger (run_id, action, vendor_id, invoice_no_norm, amount, currency, approval_id, "
        "posting_ref, invoice_date, po_ref, prior_outcome, hold_category, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            request.run_id,
            request.action,
            payload.vendor_id,
            payload.invoice_no_norm,
            amount,
            payload.currency,
            request.authorisation.id,
            posting_ref,
            payload.invoice_date.isoformat(),
            payload.po_ref,
            str(payload.outcome),
            str(payload.hold_category) if payload.hold_category else None,
            now,
        ),
    )
    for line in payload.line_quantities:
        conn.execute(
            "INSERT INTO ledger_lines (ledger_id, run_id, po_ref, po_line_ref, qty) VALUES (?, ?, ?, ?, ?)",
            (cursor.lastrowid, request.run_id, payload.po_ref, line.po_line_ref, line.qty),
        )
    return WriteOutput(status="COMMITTED", posting_ref=posting_ref, amount=payload.amount)


def submit_finance_decision(
    conn: sqlite3.Connection,
    request: SubmitFinanceDecisionInput,
    approvals: ApprovalLookup,
    clock: Clock,
    audit: Audit | None = None,
) -> WriteOutput:
    """Record one human-approved decision, exactly once for its idempotency key."""
    started = time.perf_counter()
    rule = _check_submit_permission(request, approvals)
    payload = request.payload
    replayed = False
    try:
        with transaction(conn):
            stored = conn.execute(
                "SELECT payload_hash, response_json FROM idempotency WHERE key = ?",
                (request.idempotency_key,),
            ).fetchone()
            if stored is not None:
                if stored[0] != request.payload_hash:
                    raise IdempotencyConflict("IDEMPOTENCY_CONFLICT")
                output = WriteOutput.model_validate_json(stored[1])
                replayed = True
            else:
                invoice_key = _invoice_key(payload.vendor_id, payload.invoice_no_norm)
                blocked = conn.execute(
                    "SELECT 1 FROM blocks WHERE invoice_key = ?", (invoice_key,)
                ).fetchone()
                if blocked is not None and rule.get("blocked_invoice") == "DENY":
                    raise Denied("the invoice has a payment block")
                now = clock.now_utc().isoformat()
                output = _insert_decision(conn, request, now)
                _after_ledger_insert(conn)
                conn.execute(
                    "INSERT INTO idempotency (key, payload_hash, response_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (request.idempotency_key, request.payload_hash, output.model_dump_json(), now),
                )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed: ledger.vendor_id" in str(exc):
            raise DuplicateRisk("DUPLICATE_RISK") from exc
        raise Denied("this run has already recorded this action") from exc
    _emit(
        audit,
        _event(
            "submit_finance_decision",
            "decision_submitted",
            request.run_id,
            request.action,
            "replayed" if replayed else "committed",
            clock,
            started,
            posting_ref=output.posting_ref,
            payload_hash=request.payload_hash,
        ),
    )
    return output
