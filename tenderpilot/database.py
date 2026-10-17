from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from tenderpilot.models import ContractorProfile, Tender

def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else Path(os.getenv("TENDERPILOT_DB_PATH", "data/tenderpilot.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _connection(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    connection = get_connection(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(db_path: str | Path | None = None) -> None:
    with _connection(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS contractor_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tenders (
                dedupe_key TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                tender_id TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL,
                payload TEXT NOT NULL,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_tenders_source ON tenders(source_id);
            CREATE TABLE IF NOT EXISTS saved_tenders (
                dedupe_key TEXT PRIMARY KEY REFERENCES tenders(dedupe_key) ON DELETE CASCADE,
                saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def save_profile(profile: ContractorProfile, db_path: str | Path | None = None) -> None:
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO contractor_profile (id, payload, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = CURRENT_TIMESTAMP
            """,
            (profile.model_dump_json(),),
        )


def load_profile(db_path: str | Path | None = None) -> ContractorProfile:
    with _connection(db_path) as connection:
        row = connection.execute("SELECT payload FROM contractor_profile WHERE id = 1").fetchone()
    return ContractorProfile.model_validate_json(row["payload"]) if row else ContractorProfile()


def upsert_tender(tender: Tender, db_path: str | Path | None = None) -> None:
    key = tender.dedupe_key
    payload = json.loads(tender.model_dump_json())
    with _connection(db_path) as connection:
        existing = connection.execute(
            "SELECT payload FROM tenders WHERE dedupe_key = ?", (key,)
        ).fetchone()
        if existing:
            previous = Tender.model_validate_json(existing["payload"])
            if previous.fetched_at is not None and tender.fetched_at is None:
                return
            tender.saved = previous.saved
            tender.corrigenda = list(dict.fromkeys([*previous.corrigenda, *tender.corrigenda]))
            payload = json.loads(tender.model_dump_json())
        connection.execute(
            """
            INSERT INTO tenders (dedupe_key, source_id, tender_id, source_url, payload, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(dedupe_key) DO UPDATE SET
                source_id = excluded.source_id,
                tender_id = excluded.tender_id,
                source_url = excluded.source_url,
                payload = excluded.payload,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, tender.source_id, tender.tender_id, tender.source_url, json.dumps(payload)),
        )


def list_tenders(db_path: str | Path | None = None) -> list[Tender]:
    with _connection(db_path) as connection:
        rows = connection.execute("SELECT payload FROM tenders ORDER BY updated_at DESC").fetchall()
    return [Tender.model_validate_json(row["payload"]) for row in rows]


def save_tender(dedupe_key: str, saved: bool, db_path: str | Path | None = None) -> None:
    with _connection(db_path) as connection:
        row = connection.execute("SELECT payload FROM tenders WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        if row is None:
            raise KeyError(f"Tender not found: {dedupe_key}")
        tender = Tender.model_validate_json(row["payload"])
        tender.saved = saved
        connection.execute(
            "UPDATE tenders SET payload = ?, updated_at = CURRENT_TIMESTAMP WHERE dedupe_key = ?",
            (tender.model_dump_json(), dedupe_key),
        )
        if saved:
            connection.execute("INSERT OR IGNORE INTO saved_tenders (dedupe_key) VALUES (?)", (dedupe_key,))
        else:
            connection.execute("DELETE FROM saved_tenders WHERE dedupe_key = ?", (dedupe_key,))


def list_saved_tenders(db_path: str | Path | None = None) -> list[Tender]:
    with _connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT tenders.payload FROM tenders
            INNER JOIN saved_tenders USING (dedupe_key)
            ORDER BY saved_tenders.saved_at DESC
            """
        ).fetchall()
    return [Tender.model_validate_json(row["payload"]) for row in rows]
