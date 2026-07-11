from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

import requests
from bs4 import BeautifulSoup

from .config import settings
from .models import GuideChunk, GuideDocument, ScrapeRequest
from .storage import GuideStore


class GuideClient:
    def __init__(self, store: GuideStore | None = None) -> None:
        self.store = store or GuideStore()

    def _sentence_chunks(self, text: str, max_tokens: int = 180) -> list[str]:
        sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", text) if segment.strip()]
        if not sentences:
            return []

        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0

        for sentence in sentences:
            sentence_tokens = len(sentence.split())
            if current and current_tokens + sentence_tokens > max_tokens:
                chunks.append(" ".join(current))
                current = []
                current_tokens = 0

            current.append(sentence)
            current_tokens += sentence_tokens

        if current:
            chunks.append(" ".join(current))

        return chunks

    def _fetch_html(self, url: str) -> str:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.text

    def _extract_main_text(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")

        for element in soup.select("script, style, nav, footer, header, aside, form, iframe, noscript"):
            element.decompose()

        main_node = soup.select_one("main, article, [role='main']") or soup.body
        if main_node is None:
            return ""

        lines = [node.get_text(" ", strip=True) for node in main_node.select("h1, h2, h3, p, li")]
        combined = "\n".join([line for line in lines if line])
        return re.sub(r"\s+", " ", combined).strip()

    def fetch_guide(self, request: ScrapeRequest) -> GuideDocument:
        fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        correlation_id = f"milo-{uuid4().hex[:12]}"

        html = self._fetch_html(request.guide_url)
        raw_text = self._extract_main_text(html)
        if not raw_text:
            raise ValueError("Unable to extract semantic guide content from source")

        chunks = [
            GuideChunk(chunk_index=index, heading=f"Section {index + 1}", text=chunk, token_count=len(chunk.split()))
            for index, chunk in enumerate(self._sentence_chunks(raw_text, max_tokens=180))
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
                "scrape_mode": "live",
                "source": settings.default_source,
                "chunk_count": len(chunks),
            },
        )
        self.store.record_scrape(document)
        return document
