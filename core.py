"""Shared validation rules and SQLite audit storage for onboarding runs."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).resolve().with_name("onboarding_audit.db")
_SUFFIXES = {"INC", "INCORPORATED", "LLC", "LTD", "CORP", "CORPORATION", "CO", "COMPANY"}


def normalize_business_name(name: str | None) -> str:
    """Uppercase, remove punctuation and trailing corporate designators."""
    tokens = re.findall(r"[A-Z0-9]+", (name or "").upper())
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def compare_names(left: str | None, right: str | None) -> str:
    """Return PASS, WARN_FIXABLE, or FAIL using normalized name similarity."""
    a, b = normalize_business_name(left), normalize_business_name(right)
    if not a or not b:
        return "FAIL"
    similarity = SequenceMatcher(None, a, b, autojunk=False).ratio()
    return "PASS" if similarity == 1 else "WARN_FIXABLE" if similarity >= 0.9 else "FAIL"


def validate_npi_luhn(npi: str) -> bool:
    """Validate the 10-digit NPI using the CMS 80840 Luhn prefix."""
    if not re.fullmatch(r"\d{10}", npi or ""):
        return False
    digits = "80840" + npi
    total = 0
    for offset, character in enumerate(reversed(digits)):
        value = int(character) * (2 if offset % 2 else 1)
        total += value // 10 + value % 10
    return total % 10 == 0


def validate_aba_routing(routing: str) -> bool:
    """Validate the US nine-digit ABA routing checksum."""
    if not re.fullmatch(r"\d{9}", routing or ""):
        return False
    digits = [int(character) for character in routing]
    return (3 * sum(digits[0::3]) + 7 * sum(digits[1::3]) + sum(digits[2::3])) % 10 == 0


def _connect(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: str | Path = DB_PATH) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS vendor_runs (
                run_id TEXT PRIMARY KEY,
                parent_run_id TEXT REFERENCES vendor_runs(run_id),
                vendor_name TEXT NOT NULL,
                npi TEXT NOT NULL,
                final_status TEXT NOT NULL,
                vendor_message TEXT NOT NULL,
                internal_alert TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_checks (
                check_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES vendor_runs(run_id) ON DELETE CASCADE,
                stage TEXT NOT NULL,
                check_name TEXT NOT NULL,
                outcome TEXT NOT NULL,
                expected TEXT,
                actual TEXT,
                evidence_source TEXT,
                reasoning TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_vendor_runs_created ON vendor_runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_run_checks_run ON run_checks(run_id);
        """)


def save_run(run: dict[str, Any], db_path: str | Path = DB_PATH) -> dict[str, Any]:
    """Atomically persist a run and all of its checks; return the saved record."""
    required = ("run_id", "vendor_name", "npi", "final_status", "vendor_message", "checks")
    missing = [key for key in required if key not in run]
    if missing:
        raise ValueError(f"Run is missing: {', '.join(missing)}")
    if not isinstance(run["checks"], list):
        raise ValueError("Run checks must be a list")
    record = dict(run)
    record.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    record.setdefault("internal_alert", "")
    record.setdefault("parent_run_id", None)
    init_db(db_path)
    with closing(_connect(db_path)) as connection, connection:
        if record["parent_run_id"]:
            parent = connection.execute(
                "SELECT npi FROM vendor_runs WHERE run_id = ?", (record["parent_run_id"],)
            ).fetchone()
            if parent is None:
                raise ValueError("parent_run_id does not identify an existing run")
            if parent["npi"] != record["npi"]:
                raise ValueError("Resubmission NPI differs from the parent run")
        connection.execute("""
            INSERT INTO vendor_runs
                (run_id, parent_run_id, vendor_name, npi, final_status,
                 vendor_message, internal_alert, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record["run_id"], record["parent_run_id"], record["vendor_name"],
            record["npi"], record["final_status"], record["vendor_message"],
            record["internal_alert"], record["created_at"],
            json.dumps(record, ensure_ascii=False, default=str),
        ))
        connection.executemany("""
            INSERT INTO run_checks
                (run_id, stage, check_name, outcome, expected, actual, evidence_source, reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [(
            record["run_id"], check.get("stage") or check.get("stage_name") or "Unknown",
            check.get("check_name") or "Unnamed check", check.get("outcome") or check.get("status") or "WARN_FIXABLE",
            _as_text(check.get("expected", check.get("expected_value"))),
            _as_text(check.get("actual", check.get("observed_value"))),
            _as_text(check.get("evidence_source")), _as_text(check.get("reasoning")),
        ) for check in record["checks"]])
    return record


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def get_run(run_id: str, db_path: str | Path = DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with closing(_connect(db_path)) as connection:
        row = connection.execute("SELECT payload_json FROM vendor_runs WHERE run_id = ?", (run_id,)).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_runs(limit: int = 100, offset: int = 0, db_path: str | Path = DB_PATH) -> list[dict[str, Any]]:
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("limit must be 1–500 and offset must be nonnegative")
    init_db(db_path)
    with closing(_connect(db_path)) as connection:
        rows = connection.execute("""
            SELECT payload_json FROM vendor_runs
            ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]
