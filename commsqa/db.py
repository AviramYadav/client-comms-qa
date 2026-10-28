"""SQLite schema: source-of-truth tables, issued documents, and QA results."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    address     TEXT NOT NULL,
    city        TEXT NOT NULL,
    pin         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS address_history (
    client_id   TEXT NOT NULL REFERENCES clients (client_id),
    address     TEXT NOT NULL,
    city        TEXT NOT NULL,
    pin         TEXT NOT NULL,
    valid_to    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    account_no  TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL REFERENCES clients (client_id),
    product     TEXT NOT NULL,
    currency    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS securities (
    isin        TEXT PRIMARY KEY,
    name        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    ref         TEXT PRIMARY KEY,
    account_no  TEXT NOT NULL REFERENCES accounts (account_no),
    trade_date  TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    isin        TEXT NOT NULL REFERENCES securities (isin),
    quantity    INTEGER NOT NULL,
    price       TEXT NOT NULL,      -- decimals stored as text to avoid float rounding
    brokerage   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    ref         TEXT PRIMARY KEY,
    account_no  TEXT NOT NULL REFERENCES accounts (account_no),
    value_date  TEXT NOT NULL,
    amount      TEXT NOT NULL,
    currency    TEXT NOT NULL,
    beneficiary TEXT NOT NULL,
    purpose     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_notices (
    ref            TEXT PRIMARY KEY,
    account_no     TEXT NOT NULL REFERENCES accounts (account_no),
    product        TEXT NOT NULL,
    old_rate       TEXT NOT NULL,
    new_rate       TEXT NOT NULL,
    effective_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id           TEXT PRIMARY KEY,
    doc_type         TEXT NOT NULL,
    source_ref       TEXT NOT NULL,
    client_id        TEXT NOT NULL REFERENCES clients (client_id),
    issued_on        TEXT NOT NULL,
    week             TEXT NOT NULL,
    template_version TEXT NOT NULL,
    body             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS planted_defects (
    doc_id      TEXT NOT NULL REFERENCES documents (doc_id),
    code        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qa_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    documents   INTEGER NOT NULL,
    defects     INTEGER NOT NULL,
    seconds     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS defects (
    defect_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES qa_runs (run_id),
    doc_id      TEXT NOT NULL REFERENCES documents (doc_id),
    rule        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    root_cause  TEXT NOT NULL,
    field       TEXT NOT NULL,
    expected    TEXT,
    found       TEXT,
    status      TEXT NOT NULL DEFAULT 'OPEN',
    reviewer    TEXT,
    comment     TEXT,
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_defects_doc ON defects (doc_id);
CREATE INDEX IF NOT EXISTS idx_documents_week ON documents (week);
"""

SOURCE_TABLES = ["planted_defects", "documents", "rate_notices", "payments", "trades",
                 "securities", "accounts", "address_history", "clients"]


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def reset(conn: sqlite3.Connection) -> None:
    for t in ["defects", "qa_runs"] + SOURCE_TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence")  # restart run and defect numbering
    conn.commit()
