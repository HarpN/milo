from __future__ import annotations

import sqlite3
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .embeddings import embed_text
from .config import settings

logger = logging.getLogger(__name__)


class GuideStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(db_path or settings.scrape_db_path)
        self.keeper_db_path = Path(settings.keeper_db_path)
        self._persistent_connection: sqlite3.Connection | None = None
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._persistent_connection = sqlite3.connect(":memory:", check_same_thread=False)
            self._persistent_connection.row_factory = sqlite3.Row
            self._configure_connection(self._persistent_connection)

        if settings.keeper_export_enabled and str(self.keeper_db_path) != ":memory:":
            self.keeper_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if self._persistent_connection is not None:
            connection = self._persistent_connection
            self._configure_connection(connection)
            yield connection
            connection.commit()
            return

        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            self._configure_connection(connection)
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _configure_connection(self, connection: sqlite3.Connection) -> None:
        try:
            connection.enable_load_extension(True)
            if settings.sqlite_vss_enabled and settings.sqlite_vss_extension_path:
                try:
                    connection.load_extension(settings.sqlite_vss_extension_path)
                except sqlite3.OperationalError:
                    pass
        except (sqlite3.NotSupportedError, AttributeError):
            pass
        connection.execute(f"PRAGMA busy_timeout = {settings.sqlite_busy_timeout_ms}")
        if str(self.db_path) != ":memory:":
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scrape_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guide_url TEXT NOT NULL,
                    game_title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scrape_security_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    guide_url TEXT NOT NULL,
                    game_title TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    review_status TEXT NOT NULL DEFAULT 'OPEN',
                    admin_notes TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scrape_security_event_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER,
                    action_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(event_id) REFERENCES scrape_security_events(id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_security_events_review_status_reviewed_at
                ON scrape_security_events (review_status, reviewed_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_security_events_created_at
                ON scrape_security_events (created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_security_audit_event_id
                ON scrape_security_event_audit (event_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_security_audit_created_at
                ON scrape_security_event_audit (created_at)
                """
            )
            self._ensure_local_column(
                connection,
                "scrape_security_events",
                "review_status",
                "TEXT NOT NULL DEFAULT 'OPEN'",
            )
            self._ensure_local_column(
                connection,
                "scrape_security_events",
                "admin_notes",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_local_column(
                connection,
                "scrape_security_events",
                "reviewed_at",
                "TEXT",
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guide_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    heading TEXT NOT NULL,
                    text TEXT NOT NULL,
                    token_count INTEGER NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES scrape_jobs(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guide_chunk_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    embedding_json TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES scrape_jobs(id),
                    UNIQUE(job_id, chunk_index)
                )
                """
            )
        self._initialize_keeper()

    @contextmanager
    def keeper_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.keeper_db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            self._configure_connection(connection)
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize_keeper(self) -> None:
        if not settings.keeper_export_enabled:
            return

        with self.keeper_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS keeper_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_agent TEXT NOT NULL,
                    guide_url TEXT NOT NULL,
                    game_title TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    heading TEXT NOT NULL,
                    text TEXT NOT NULL,
                    token_count INTEGER NOT NULL,
                    trust_status TEXT NOT NULL DEFAULT 'approved',
                    trust_confidence REAL NOT NULL DEFAULT 1.0,
                    source_domain TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    sanitizer_version TEXT NOT NULL DEFAULT '',
                    safety_notes TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._ensure_keeper_column(connection, "keeper_chunks", "trust_status", "TEXT NOT NULL DEFAULT 'approved'")
            self._ensure_keeper_column(connection, "keeper_chunks", "trust_confidence", "REAL NOT NULL DEFAULT 1.0")
            self._ensure_keeper_column(connection, "keeper_chunks", "source_domain", "TEXT NOT NULL DEFAULT ''")
            self._ensure_keeper_column(connection, "keeper_chunks", "content_hash", "TEXT NOT NULL DEFAULT ''")
            self._ensure_keeper_column(connection, "keeper_chunks", "sanitizer_version", "TEXT NOT NULL DEFAULT ''")
            self._ensure_keeper_column(connection, "keeper_chunks", "safety_notes", "TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS keeper_guides (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_agent TEXT NOT NULL,
                    guide_url TEXT NOT NULL,
                    game_title TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    quality_views INTEGER NOT NULL DEFAULT 0,
                    quality_age_days INTEGER NOT NULL DEFAULT 0,
                    quality_score REAL NOT NULL DEFAULT 0,
                    trust_status TEXT NOT NULL DEFAULT 'approved',
                    trust_confidence REAL NOT NULL DEFAULT 1.0,
                    source_domain TEXT NOT NULL DEFAULT '',
                    sanitizer_version TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(guide_url)
                )
                """
            )
            self._ensure_keeper_column(connection, "keeper_guides", "trust_status", "TEXT NOT NULL DEFAULT 'approved'")
            self._ensure_keeper_column(connection, "keeper_guides", "trust_confidence", "REAL NOT NULL DEFAULT 1.0")
            self._ensure_keeper_column(connection, "keeper_guides", "source_domain", "TEXT NOT NULL DEFAULT ''")
            self._ensure_keeper_column(connection, "keeper_guides", "sanitizer_version", "TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS keeper_chunk_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_agent TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    embedding_json TEXT NOT NULL,
                    UNIQUE(correlation_id, chunk_index)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS keeper_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    version_label TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK (version_label IN ('LATEST', 'PREVIOUS', 'STABLE')),
                    UNIQUE(entity_type, entity_key, version_label)
                )
                """
            )

    def _ensure_keeper_column(self, connection: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in existing:
            return
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    def _ensure_local_column(self, connection: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in existing:
            return
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    def _promote_snapshots(
        self,
        connection: sqlite3.Connection,
        *,
        entity_type: str,
        entity_key: str,
        latest_payload_json: str,
        created_at: str,
    ) -> None:
        current_latest = connection.execute(
            """
            SELECT payload_json, created_at
            FROM keeper_snapshots
            WHERE entity_type = ? AND entity_key = ? AND version_label = 'LATEST'
            """,
            (entity_type, entity_key),
        ).fetchone()

        if current_latest is not None and str(current_latest["payload_json"]) != latest_payload_json:
            connection.execute(
                """
                INSERT OR REPLACE INTO keeper_snapshots (
                    entity_type, entity_key, version_label, payload_json, created_at
                ) VALUES (?, ?, 'PREVIOUS', ?, ?)
                """,
                (entity_type, entity_key, str(current_latest["payload_json"]), str(current_latest["created_at"])),
            )

            has_stable = connection.execute(
                """
                SELECT 1
                FROM keeper_snapshots
                WHERE entity_type = ? AND entity_key = ? AND version_label = 'STABLE'
                """,
                (entity_type, entity_key),
            ).fetchone()
            if has_stable is None:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO keeper_snapshots (
                        entity_type, entity_key, version_label, payload_json, created_at
                    ) VALUES (?, ?, 'STABLE', ?, ?)
                    """,
                    (entity_type, entity_key, str(current_latest["payload_json"]), str(current_latest["created_at"])),
                )

        connection.execute(
            """
            INSERT OR REPLACE INTO keeper_snapshots (
                entity_type, entity_key, version_label, payload_json, created_at
            ) VALUES (?, ?, 'LATEST', ?, ?)
            """,
            (entity_type, entity_key, latest_payload_json, created_at),
        )

    def record_scrape(self, guide_document) -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scrape_jobs (guide_url, game_title, source, fetched_at, correlation_id, summary)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    guide_document.guide_url,
                    guide_document.game_title,
                    guide_document.source,
                    guide_document.fetched_at,
                    guide_document.correlation_id,
                    guide_document.summary,
                ),
            )
            job_id = cursor.lastrowid
            for chunk in guide_document.chunks:
                connection.execute(
                    """
                    INSERT INTO guide_chunks (job_id, chunk_index, heading, text, token_count)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (job_id, chunk.chunk_index, chunk.heading, chunk.text, chunk.token_count),
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO guide_chunk_embeddings (job_id, chunk_index, embedding_json)
                    VALUES (?, ?, ?)
                    """,
                    (job_id, chunk.chunk_index, json.dumps(embed_text(chunk.text), separators=(",", ":"))),
                )

        if settings.keeper_export_enabled:
            with self.keeper_connection() as keeper_connection:
                keeper_connection.execute(
                    """
                    INSERT OR REPLACE INTO keeper_guides (
                        source_agent, guide_url, game_title, platform,
                        quality_views, quality_age_days, quality_score,
                        trust_status, trust_confidence, source_domain, sanitizer_version,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "milo",
                        guide_document.guide_url,
                        guide_document.game_title,
                        guide_document.platform,
                        guide_document.quality_views,
                        guide_document.quality_age_days,
                        guide_document.quality_score,
                        guide_document.trust_status,
                        guide_document.trust_confidence,
                        guide_document.source_domain,
                        guide_document.sanitizer_version,
                        guide_document.fetched_at,
                    ),
                )

                snapshot_payload = {
                    "guide_url": guide_document.guide_url,
                    "game_title": guide_document.game_title,
                    "platform": guide_document.platform,
                    "quality_views": int(guide_document.quality_views),
                    "quality_age_days": int(guide_document.quality_age_days),
                    "quality_score": float(guide_document.quality_score),
                    "trust_status": guide_document.trust_status,
                    "trust_confidence": float(guide_document.trust_confidence),
                    "source_domain": guide_document.source_domain,
                    "sanitizer_version": guide_document.sanitizer_version,
                    "correlation_id": guide_document.correlation_id,
                    "chunk_count": len(guide_document.chunks),
                }
                entity_key = f"{guide_document.guide_url}::{guide_document.platform}"
                self._promote_snapshots(
                    keeper_connection,
                    entity_type="guide",
                    entity_key=entity_key,
                    latest_payload_json=json.dumps(snapshot_payload, separators=(",", ":")),
                    created_at=guide_document.fetched_at,
                )

                for chunk in guide_document.chunks:
                    embedding_json = json.dumps(embed_text(chunk.text), separators=(",", ":"))
                    keeper_connection.execute(
                        """
                        INSERT INTO keeper_chunks (
                            source_agent, guide_url, game_title, correlation_id, fetched_at,
                            chunk_index, heading, text, token_count,
                            trust_status, trust_confidence, source_domain, content_hash,
                            sanitizer_version, safety_notes
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "milo",
                            guide_document.guide_url,
                            guide_document.game_title,
                            guide_document.correlation_id,
                            guide_document.fetched_at,
                            chunk.chunk_index,
                            chunk.heading,
                            chunk.text,
                            chunk.token_count,
                            chunk.trust_status,
                            chunk.trust_confidence,
                            chunk.source_domain,
                            chunk.content_hash,
                            chunk.sanitizer_version,
                            chunk.safety_notes,
                        ),
                    )
                    keeper_connection.execute(
                        """
                        INSERT OR REPLACE INTO keeper_chunk_embeddings (
                            source_agent, correlation_id, chunk_index, embedding_json
                        ) VALUES (?, ?, ?, ?)
                        """,
                        ("milo", guide_document.correlation_id, chunk.chunk_index, embedding_json),
                    )

        return int(job_id)

    def record_security_event(
        self,
        *,
        event_type: str,
        severity: str,
        guide_url: str,
        game_title: str,
        reason: str,
        details_json: dict,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO scrape_security_events (
                    event_type, severity, guide_url, game_title, reason, details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_type, severity, guide_url, game_title, reason, json.dumps(details_json, separators=(",", ":"))),
            )
        logger.warning("Milo security event: %s [%s] %s (%s)", event_type, severity, reason, guide_url)

    def summary(self) -> dict[str, int]:
        with self.connection() as connection:
            scrape_jobs = connection.execute("SELECT COUNT(*) AS total FROM scrape_jobs").fetchone()["total"]
            guide_chunks = connection.execute("SELECT COUNT(*) AS total FROM guide_chunks").fetchone()["total"]
            blocked = connection.execute("SELECT COUNT(*) AS total FROM scrape_security_events").fetchone()["total"]
            return {"scrape_jobs": int(scrape_jobs), "guide_chunks": int(guide_chunks), "security_events": int(blocked)}

    def list_security_events(self, limit: int = 50) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    event_type,
                    severity,
                    guide_url,
                    game_title,
                    reason,
                    details_json,
                    review_status,
                    admin_notes,
                    reviewed_at,
                    created_at
                FROM scrape_security_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_security_event_audit(self, limit: int = 20) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    audit.id,
                    audit.event_id,
                    audit.action_type,
                    audit.actor,
                    audit.notes,
                    audit.details_json,
                    audit.created_at,
                    events.event_type,
                    events.severity,
                    events.game_title,
                    events.guide_url
                FROM scrape_security_event_audit audit
                LEFT JOIN scrape_security_events events
                  ON events.id = audit.event_id
                ORDER BY audit.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def review_security_event(self, event_id: int, review_status: str, admin_notes: str = "", actor: str = "admin") -> bool:
        normalized_status = review_status.strip().upper()
        if normalized_status not in {"OPEN", "ACKNOWLEDGED", "RESOLVED"}:
            raise ValueError("review_status must be one of OPEN, ACKNOWLEDGED, RESOLVED")

        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE scrape_security_events
                SET review_status = ?, admin_notes = ?, reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (normalized_status, admin_notes[:500], int(event_id)),
            )
            updated = int(cursor.rowcount) > 0
            if updated:
                connection.execute(
                    """
                    INSERT INTO scrape_security_event_audit (
                        event_id, action_type, actor, notes, details_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        int(event_id),
                        "REVIEW_STATUS_UPDATE",
                        actor[:128],
                        admin_notes[:500],
                        json.dumps({"review_status": normalized_status}, separators=(",", ":")),
                    ),
                )
            return updated

    def apply_security_event_retention(self, retention_days: int, dry_run: bool = False, actor: str = "admin") -> int:
        days = max(1, int(retention_days))
        with self.connection() as connection:
            count_row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM scrape_security_events
                WHERE review_status = 'RESOLVED'
                  AND reviewed_at IS NOT NULL
                  AND datetime(reviewed_at) < datetime('now', ?)
                """,
                (f"-{days} days",),
            ).fetchone()
            would_delete = int(count_row["total"] if count_row else 0)

            if dry_run:
                connection.execute(
                    """
                    INSERT INTO scrape_security_event_audit (
                        event_id, action_type, actor, notes, details_json
                    ) VALUES (NULL, ?, ?, ?, ?)
                    """,
                    (
                        "RETENTION_DRY_RUN",
                        actor[:128],
                        f"Retention dry-run for {days} day(s)",
                        json.dumps({"retention_days": days, "would_delete": would_delete}, separators=(",", ":")),
                    ),
                )
                return would_delete

            connection.execute(
                """
                DELETE FROM scrape_security_events
                WHERE review_status = 'RESOLVED'
                  AND reviewed_at IS NOT NULL
                  AND datetime(reviewed_at) < datetime('now', ?)
                """,
                (f"-{days} days",),
            )
            connection.execute(
                """
                INSERT INTO scrape_security_event_audit (
                    event_id, action_type, actor, notes, details_json
                ) VALUES (NULL, ?, ?, ?, ?)
                """,
                (
                    "RETENTION_APPLY",
                    actor[:128],
                    f"Retention applied for {days} day(s)",
                    json.dumps({"retention_days": days, "deleted_count": would_delete}, separators=(",", ":")),
                ),
            )
            return would_delete
