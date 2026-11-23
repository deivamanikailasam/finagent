"""Repositories over the application database. SQL stays here; rules stay in the services."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from finagent.config.clock import SystemClock
from finagent.obs.redaction import redact

_AUDIT_COLUMNS = {"ts", "run_id", "event", "outcome", "duration_ms"}


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class RunRow:
    run_id: str
    case_id: str
    status: str
    current_node: str
    as_of: datetime
    corpus_hash: str | None
    prompt_hash: str | None
    config_hash: str | None
    model_id: str | None
    result_json: str | None


class RunRepo:
    _COLUMNS = (
        "run_id, case_id, status, current_node, as_of, corpus_hash, prompt_hash, "
        "config_hash, model_id, result_json"
    )

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(
        self, run_id: str, case_id: str, status: str, node: str, as_of: datetime, now: datetime
    ) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, case_id, status, current_node, as_of, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, case_id, status, node, as_of.isoformat(), now.isoformat(), now.isoformat()),
        )

    def get(self, run_id: str) -> RunRow | None:
        row = self._conn.execute(f"SELECT {self._COLUMNS} FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunRow(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), *row[5:])

    def update(
        self, run_id: str, status: str, node: str, now: datetime, result_json: str | None = None
    ) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, current_node = ?, updated_at = ?, "
            "result_json = COALESCE(?, result_json) WHERE run_id = ?",
            (status, node, now.isoformat(), result_json, run_id),
        )

    def record_hashes(
        self, run_id: str, corpus_hash: str, prompt_hash: str | None, config_hash: str, model_id: str | None
    ) -> None:
        self._conn.execute(
            "UPDATE runs SET corpus_hash = ?, prompt_hash = ?, config_hash = ?, model_id "
            "= ? WHERE run_id = ?",
            (corpus_hash, prompt_hash, config_hash, model_id, run_id),
        )


class CheckpointRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(self, run_id: str, node: str, state_json: str, now: datetime) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM checkpoints WHERE run_id = ?", (run_id,)
        ).fetchone()
        self._conn.execute(
            "INSERT INTO checkpoints (run_id, seq, node, state_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, row[0], node, state_json, now.isoformat()),
        )
        return row[0]

    def latest(self, run_id: str) -> tuple[int, str, str] | None:
        row = self._conn.execute(
            "SELECT seq, node, state_json FROM checkpoints WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return tuple(row) if row else None

    def all(self, run_id: str) -> list[tuple[int, str, str]]:
        rows = self._conn.execute(
            "SELECT seq, node, state_json FROM checkpoints WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
        return [tuple(row) for row in rows]


class AuditRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(self, event: dict[str, Any]) -> None:
        """Store one event. Fields beyond the required five are redacted before they are written."""
        detail = {key: value for key, value in event.items() if key not in _AUDIT_COLUMNS}
        # ts is the case's business time; this is when the event really happened.
        detail["recorded_at"] = SystemClock().now_utc().isoformat()
        self._conn.execute(
            "INSERT INTO audit (ts, run_id, event, outcome, duration_ms, detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event["ts"],
                event.get("run_id"),
                event["event"],
                str(event["outcome"]),
                float(event["duration_ms"]),
                json.dumps(redact(detail), default=str, sort_keys=True),
            ),
        )

    def events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT ts, run_id, event, outcome, duration_ms, detail_json FROM audit "
            "WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [
            {
                **json.loads(row[5]),
                "ts": row[0],
                "run_id": row[1],
                "event": row[2],
                "outcome": row[3],
                "duration_ms": row[4],
            }
            for row in rows
        ]


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    run_id: str
    case_id: str
    action: str
    vendor_id: str
    vendor_name: str
    invoice_no_norm: str
    amount: Decimal
    currency: str
    payload_json: str
    payload_hash: str
    requester: str
    receipters: tuple[str, ...]
    exceptions: tuple[Any, ...]
    citations: tuple[str, ...]
    snapshot: dict[str, Any] | None
    status: str
    requested_at: datetime
    decided_at: datetime | None
    revalidated_at: datetime | None
    approver_id: str | None
    role: str | None
    limit_amount: Decimal | None
    register_version: str | None
    decision: str | None
    reason: str | None
    response_json: str | None

    @property
    def outcome(self) -> str:
        """The recommended outcome, read from the payload the approval is bound to."""
        return json.loads(self.payload_json)["outcome"]

    @property
    def commitment(self) -> Decimal:
        """The amount an approver's limit must cover: the larger of invoice gross and PO total."""
        stored = json.loads(self.payload_json).get("commitment")
        return Decimal(stored) if stored is not None else self.amount


@dataclass(frozen=True)
class EscalationRecord:
    approval_id: str
    run_id: str
    approver_id: str
    routed_to: str
    reason: str
    created_at: datetime


class ApprovalRepo:
    _COLUMNS = (
        "approval_id, run_id, case_id, action, vendor_id, vendor_name, invoice_no_norm, amount, currency, "
        "payload_json, payload_hash, requester, receipters_json, exceptions_json, "
        "citations_json, snapshot_json, "
        "status, requested_at, decided_at, revalidated_at, approver_id, role, "
        "limit_amount, register_version, "
        "decision, reason, response_json"
    )

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _record(row: tuple[Any, ...]) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row[0],
            run_id=row[1],
            case_id=row[2],
            action=row[3],
            vendor_id=row[4],
            vendor_name=row[5],
            invoice_no_norm=row[6],
            amount=Decimal(row[7]),
            currency=row[8],
            payload_json=row[9],
            payload_hash=row[10],
            requester=row[11],
            receipters=tuple(json.loads(row[12])),
            exceptions=tuple(json.loads(row[13])),
            citations=tuple(json.loads(row[14])),
            snapshot=json.loads(row[15]) if row[15] else None,
            status=row[16],
            requested_at=datetime.fromisoformat(row[17]),
            decided_at=_when(row[18]),
            revalidated_at=_when(row[19]),
            approver_id=row[20],
            role=row[21],
            limit_amount=Decimal(row[22]) if row[22] is not None else None,
            register_version=row[23],
            decision=row[24],
            reason=row[25],
            response_json=row[26],
        )

    def get(self, approval_id: str) -> ApprovalRecord | None:
        row = self._conn.execute(
            f"SELECT {self._COLUMNS} FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        return self._record(row) if row else None

    def pending_for(self, vendor_id: str, invoice_no_norm: str) -> ApprovalRecord | None:
        row = self._conn.execute(
            f"SELECT {self._COLUMNS} FROM approvals "
            "WHERE vendor_id = ? AND invoice_no_norm = ? AND status = 'PENDING'",
            (vendor_id, invoice_no_norm),
        ).fetchone()
        return self._record(row) if row else None

    def insert(self, values: dict[str, Any]) -> None:
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        self._conn.execute(
            f"INSERT INTO approvals ({columns}) VALUES ({marks})",
            tuple(values.values()),
        )

    def decide(self, approval_id: str, status: str, fields: dict[str, Any]) -> bool:
        """Compare-and-set: only one decision can ever move a request out of PENDING."""
        assignments = ", ".join(f"{name} = ?" for name in fields)
        cursor = self._conn.execute(
            f"UPDATE approvals SET status = ?, {assignments} WHERE approval_id = ? AND status = 'PENDING'",
            (status, *fields.values(), approval_id),
        )
        return cursor.rowcount == 1

    def retire_pending(self, approval_id: str, now: datetime) -> bool:
        """Compare-and-set: a stricter recommendation replaces a request nobody has decided."""
        cursor = self._conn.execute(
            "UPDATE approvals SET status = 'INVALIDATED', revalidated_at = ? "
            "WHERE approval_id = ? AND status = 'PENDING'",
            (now.isoformat(), approval_id),
        )
        return cursor.rowcount == 1

    def mark_revalidated(self, approval_id: str, valid: bool, now: datetime) -> bool:
        """Compare-and-set from APPROVED, so a stale check cannot revive another state."""
        status = "APPROVED" if valid else "INVALIDATED"
        cursor = self._conn.execute(
            "UPDATE approvals SET status = ?, revalidated_at = ? WHERE approval_id = ? "
            "AND status = 'APPROVED'",
            (status, now.isoformat(), approval_id),
        )
        return cursor.rowcount == 1

    def add_escalation(
        self, approval_id: str, run_id: str, approver_id: str, routed_to: str, reason: str, now: datetime
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO escalations (approval_id, run_id, approver_id, "
            "routed_to, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, run_id, approver_id, routed_to, reason, now.isoformat()),
        )

    def escalations(self) -> list[EscalationRecord]:
        rows = self._conn.execute(
            "SELECT approval_id, run_id, approver_id, routed_to, reason, created_at FROM "
            "escalations ORDER BY id"
        ).fetchall()
        return [EscalationRecord(*row[:5], datetime.fromisoformat(row[5])) for row in rows]
