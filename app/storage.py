from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .embeddings import embed_text
from .config import settings


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
                    token_count INTEGER NOT NULL
                )
                """
            )
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
                for chunk in guide_document.chunks:
                    embedding_json = json.dumps(embed_text(chunk.text), separators=(",", ":"))
                    keeper_connection.execute(
                        """
                        INSERT INTO keeper_chunks (
                            source_agent, guide_url, game_title, correlation_id, fetched_at,
                            chunk_index, heading, text, token_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def summary(self) -> dict[str, int]:
        with self.connection() as connection:
            scrape_jobs = connection.execute("SELECT COUNT(*) AS total FROM scrape_jobs").fetchone()["total"]
            guide_chunks = connection.execute("SELECT COUNT(*) AS total FROM guide_chunks").fetchone()["total"]
            return {"scrape_jobs": int(scrape_jobs), "guide_chunks": int(guide_chunks)}
