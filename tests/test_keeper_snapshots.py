from __future__ import annotations

import json
import sqlite3

from app.config import settings
from app.models import GuideChunk, GuideDocument
from app.storage import GuideStore


def _snapshot_map(db_path: str, entity_key: str) -> dict[str, dict]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT version_label, payload_json
            FROM keeper_snapshots
            WHERE entity_type = 'guide' AND entity_key = ?
            """,
            (entity_key,),
        ).fetchall()
    return {str(row["version_label"]): json.loads(str(row["payload_json"])) for row in rows}


def _doc(correlation_id: str, quality_views: int, quality_score: float, fetched_at: str) -> GuideDocument:
    return GuideDocument(
        guide_url="https://example.com/astro-bot",
        game_title="Astro Bot",
        platform="PS5",
        source="web",
        fetched_at=fetched_at,
        correlation_id=correlation_id,
        summary="Snapshot rotation test",
        quality_views=quality_views,
        quality_age_days=10,
        quality_score=quality_score,
        chunks=[GuideChunk(chunk_index=0, heading="H1", text="Collect all hidden bots first.", token_count=6)],
    )


def test_snapshot_promotion_for_guide_exports(tmp_path, monkeypatch) -> None:
    keeper_db = tmp_path / "keeper_milo.db"
    monkeypatch.setattr(settings, "keeper_export_enabled", True)
    monkeypatch.setattr(settings, "keeper_db_path", str(keeper_db))
    monkeypatch.setattr(settings, "scrape_db_path", ":memory:")

    store = GuideStore(db_path=":memory:")

    store.record_scrape(_doc("guide-a", 50000, 0.55, "2026-07-11T00:00:00+00:00"))
    key = "https://example.com/astro-bot::PS5"
    snapshots = _snapshot_map(str(keeper_db), key)
    assert set(snapshots.keys()) == {"LATEST"}
    assert snapshots["LATEST"]["correlation_id"] == "guide-a"

    store.record_scrape(_doc("guide-b", 120000, 0.71, "2026-07-11T00:05:00+00:00"))
    snapshots = _snapshot_map(str(keeper_db), key)
    assert set(snapshots.keys()) == {"LATEST", "PREVIOUS", "STABLE"}
    assert snapshots["LATEST"]["correlation_id"] == "guide-b"
    assert snapshots["PREVIOUS"]["correlation_id"] == "guide-a"
    assert snapshots["STABLE"]["correlation_id"] == "guide-a"

    store.record_scrape(_doc("guide-c", 200000, 0.83, "2026-07-11T00:10:00+00:00"))
    snapshots = _snapshot_map(str(keeper_db), key)
    assert snapshots["LATEST"]["correlation_id"] == "guide-c"
    assert snapshots["PREVIOUS"]["correlation_id"] == "guide-b"
    assert snapshots["STABLE"]["correlation_id"] == "guide-a"
