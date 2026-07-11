from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings


class GuideStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(db_path or settings.scrape_db_path)
        self._persistent_connection: sqlite3.Connection | None = None
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._persistent_connection = sqlite3.connect(":memory:", check_same_thread=False)
            self._persistent_connection.row_factory = sqlite3.Row
            self._configure_connection(self._persistent_connection)
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
            return int(job_id)

    def summary(self) -> dict[str, int]:
        with self.connection() as connection:
            scrape_jobs = connection.execute("SELECT COUNT(*) AS total FROM scrape_jobs").fetchone()["total"]
            guide_chunks = connection.execute("SELECT COUNT(*) AS total FROM guide_chunks").fetchone()["total"]
            return {"scrape_jobs": int(scrape_jobs), "guide_chunks": int(guide_chunks)}
