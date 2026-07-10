from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .config import settings
from .models import GuideChunk, GuideDocument, ScrapeRequest
from .storage import GuideStore


class GuideClient:
    def __init__(self, store: GuideStore | None = None) -> None:
        self.store = store or GuideStore()

    def _chunk_text(self, text: str, chunk_size: int = 240) -> list[str]:
        words = text.split()
        if not words:
            return []
        chunks: list[str] = []
        for index in range(0, len(words), chunk_size):
            chunks.append(" ".join(words[index:index + chunk_size]))
        return chunks

    def fetch_guide(self, request: ScrapeRequest) -> GuideDocument:
        fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        correlation_id = f"milo-{uuid4().hex[:12]}"
        raw_text = (
            f"{request.game_title} guide sourced from {request.guide_url}. "
            "Start with the core loop, identify progression gates, then route players into the lowest-friction path. "
            "Break down routes into setup, execution, recovery, and optimization notes."
        )
        chunks = [
            GuideChunk(chunk_index=index, heading=f"Section {index + 1}", text=chunk, token_count=len(chunk.split()))
            for index, chunk in enumerate(self._chunk_text(raw_text, chunk_size=24))
        ]
        document = GuideDocument(
            guide_url=request.guide_url,
            game_title=request.game_title,
            source=request.source or settings.default_source,
            fetched_at=fetched_at,
            correlation_id=correlation_id,
            summary="Guide extraction, normalization, and chunking complete.",
            chunks=chunks,
            raw_payload={
                "guide_url": request.guide_url,
                "scrape_mode": "mock",
                "source": settings.default_source,
                "chunk_count": len(chunks),
            },
        )
        self.store.record_scrape(document)
        return document
