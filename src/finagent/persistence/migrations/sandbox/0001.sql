-- Simulated ERP sandbox. Ledger, line and block rows are append-only (decision log VI, XVIII).
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    action TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    invoice_no_norm TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    posting_ref TEXT NOT NULL,
    invoice_date TEXT NOT NULL,
    po_ref TEXT,
    prior_outcome TEXT NOT NULL,
    hold_category TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, action)
);

CREATE UNIQUE INDEX IF NOT EXISTS ledger_one_post_per_invoice
    ON ledger (vendor_id, invoice_no_norm) WHERE action = 'POST';

CREATE TABLE IF NOT EXISTS ledger_lines (
    id INTEGER PRIMARY KEY,
    ledger_id INTEGER NOT NULL REFERENCES ledger (id),
    run_id TEXT NOT NULL,
    po_ref TEXT,
    po_line_ref TEXT NOT NULL,
    qty INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'BLOCK',
    rule_id TEXT NOT NULL,
    invoice_key TEXT NOT NULL,
    authorisation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, action)
);

CREATE INDEX IF NOT EXISTS blocks_by_invoice ON blocks (invoice_key);

CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS ledger_lines_no_update BEFORE UPDATE ON ledger_lines
BEGIN SELECT RAISE(ABORT, 'ledger_lines is append-only'); END;

CREATE TRIGGER IF NOT EXISTS ledger_lines_no_delete BEFORE DELETE ON ledger_lines
BEGIN SELECT RAISE(ABORT, 'ledger_lines is append-only'); END;

CREATE TRIGGER IF NOT EXISTS blocks_no_update BEFORE UPDATE ON blocks
BEGIN SELECT RAISE(ABORT, 'blocks is append-only'); END;

CREATE TRIGGER IF NOT EXISTS blocks_no_delete BEFORE DELETE ON blocks
BEGIN SELECT RAISE(ABORT, 'blocks is append-only'); END;
