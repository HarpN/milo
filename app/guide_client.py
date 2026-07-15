from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import requests
from bs4 import BeautifulSoup

from .config import settings
from .models import GuideChunk, GuideDocument, ScrapeRequest
from .sanitizer import content_hash, sanitize_text, validate_source_url
from .storage import GuideStore


class GuideClient:
    def __init__(self, store: GuideStore | None = None) -> None:
        self.store = store or GuideStore()
        self._domain_last_fetch: dict[str, float] = {}
        self._domain_lock = threading.Lock()

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
        max_bytes = max(1024, int(settings.max_response_bytes))
        response = requests.get(url, timeout=15, stream=True)
        response.raise_for_status()

        chunks: list[bytes] = []
        total = 0
        for block in response.iter_content(chunk_size=16384):
            if not block:
                continue
            total += len(block)
            if total > max_bytes:
                raise ValueError(f"Response exceeded max size cap ({max_bytes} bytes)")
            chunks.append(block)

        encoding = response.encoding or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")

    def _enforce_domain_cooldown(self, domain: str) -> tuple[bool, float]:
        cooldown = max(0.0, float(settings.domain_request_cooldown_seconds))
        if cooldown <= 0:
            return True, 0.0

        now = time.monotonic()
        with self._domain_lock:
            previous = self._domain_last_fetch.get(domain)
            if previous is not None:
                elapsed = now - previous
                if elapsed < cooldown:
                    remaining = round(cooldown - elapsed, 3)
                    return False, remaining
            self._domain_last_fetch[domain] = now
        return True, 0.0

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

        url_validation = validate_source_url(request.guide_url)
        if not url_validation.ok:
            self.store.record_security_event(
                event_type="SOURCE_REJECTED",
                severity="HIGH",
                guide_url=request.guide_url,
                game_title=request.game_title,
                reason=url_validation.reason,
                details_json={"domain": url_validation.domain, "correlation_id": correlation_id},
            )
            raise ValueError(f"Source rejected: {url_validation.reason}")

        allowed, wait_seconds = self._enforce_domain_cooldown(url_validation.domain)
        if not allowed:
            self.store.record_security_event(
                event_type="SOURCE_RATE_LIMITED",
                severity="MEDIUM",
                guide_url=request.guide_url,
                game_title=request.game_title,
                reason=f"Domain cooldown active for {wait_seconds}s",
                details_json={
                    "domain": url_validation.domain,
                    "correlation_id": correlation_id,
                    "cooldown_seconds": settings.domain_request_cooldown_seconds,
                    "wait_seconds": wait_seconds,
                },
            )
            raise ValueError(f"Domain cooldown active for {wait_seconds}s")

        try:
            html = self._fetch_html(request.guide_url)
        except ValueError as exc:
            self.store.record_security_event(
                event_type="FETCH_REJECTED",
                severity="HIGH",
                guide_url=request.guide_url,
                game_title=request.game_title,
                reason=str(exc),
                details_json={"domain": url_validation.domain, "correlation_id": correlation_id},
            )
            raise
        except requests.RequestException as exc:
            self.store.record_security_event(
                event_type="FETCH_FAILED",
                severity="MEDIUM",
                guide_url=request.guide_url,
                game_title=request.game_title,
                reason=str(exc),
                details_json={"domain": url_validation.domain, "correlation_id": correlation_id},
            )
            raise ValueError(f"Fetch failed: {exc}")

        raw_text = self._extract_main_text(html)
        if not raw_text:
            self.store.record_security_event(
                event_type="EXTRACTION_REJECTED",
                severity="MEDIUM",
                guide_url=request.guide_url,
                game_title=request.game_title,
                reason="No semantic text extracted",
                details_json={"correlation_id": correlation_id},
            )
            raise ValueError("Unable to extract semantic guide content from source")

        sanitization = sanitize_text(raw_text)
        if sanitization.suspicious:
            self.store.record_security_event(
                event_type="CONTENT_REJECTED",
                severity="HIGH",
                guide_url=request.guide_url,
                game_title=request.game_title,
                reason=sanitization.reason,
                details_json={"correlation_id": correlation_id, "confidence": sanitization.confidence},
            )
            raise ValueError(f"Sanitization rejected content: {sanitization.reason}")

        views_score = min(max(request.quality_views / 250000, 0), 1)
        recency_score = max(0.0, 1.0 - min(request.quality_age_days, 365) / 365)
        quality_score = round((0.7 * views_score) + (0.3 * recency_score), 4)
        trust_confidence = round(min(quality_score, sanitization.confidence), 4)

        chunks = [
            GuideChunk(
                chunk_index=index,
                heading=f"Section {index + 1}",
                text=chunk,
                token_count=len(chunk.split()),
                trust_status="approved",
                trust_confidence=trust_confidence,
                source_domain=url_validation.domain,
                content_hash=content_hash(chunk),
                sanitizer_version=settings.sanitizer_version,
                safety_notes="Approved by sanitizer and source allowlist",
            )
            for index, chunk in enumerate(self._sentence_chunks(sanitization.sanitized_text, max_tokens=180))
        ]
        document = GuideDocument(
            guide_url=request.guide_url,
            game_title=request.game_title,
            platform=request.platform,
            source=request.source or settings.default_source,
            fetched_at=fetched_at,
            correlation_id=correlation_id,
            summary="Guide extraction, normalization, and chunking complete.",
            quality_views=request.quality_views,
            quality_age_days=request.quality_age_days,
            quality_score=quality_score,
            chunks=chunks,
            trust_status="approved",
            trust_confidence=trust_confidence,
            source_domain=url_validation.domain,
            sanitizer_version=settings.sanitizer_version,
            raw_payload={
                "guide_url": request.guide_url,
                "scrape_mode": "live",
                "source": settings.default_source,
                "chunk_count": len(chunks),
                "platform": request.platform,
                "quality_views": request.quality_views,
                "quality_age_days": request.quality_age_days,
                "quality_score": quality_score,
                "trust_confidence": trust_confidence,
                "source_domain": url_validation.domain,
                "sanitizer_version": settings.sanitizer_version,
            },
        )
        self.store.record_scrape(document)
        return document
