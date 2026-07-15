from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .config import settings

_SUSPICIOUS_PATTERNS = (
    r"<script",
    r"javascript:",
    r"onerror\s*=",
    r"onload\s*=",
    r"ignore\s+previous\s+instructions",
    r"system\s+prompt",
    r"rm\s+-rf",
    r"drop\s+table",
    r"union\s+select",
)


@dataclass(frozen=True)
class UrlValidationResult:
    ok: bool
    domain: str
    reason: str


@dataclass(frozen=True)
class SanitizationResult:
    sanitized_text: str
    suspicious: bool
    reason: str
    confidence: float


def validate_source_url(url: str) -> UrlValidationResult:
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() != "https":
        return UrlValidationResult(ok=False, domain="", reason="Only https sources are allowed")

    domain = (parsed.hostname or "").lower().strip()
    if not domain:
        return UrlValidationResult(ok=False, domain="", reason="Source domain is missing")

    if domain not in settings.allowed_guide_domains:
        return UrlValidationResult(ok=False, domain=domain, reason=f"Domain '{domain}' is not allowlisted")

    return UrlValidationResult(ok=True, domain=domain, reason="ok")


def sanitize_text(text: str) -> SanitizationResult:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return SanitizationResult(sanitized_text="", suspicious=True, reason="No semantic text extracted", confidence=0.0)

    if len(normalized) > settings.max_chunk_chars * 20:
        normalized = normalized[: settings.max_chunk_chars * 20]

    lowered = normalized.lower()
    for pattern in _SUSPICIOUS_PATTERNS:
        if re.search(pattern, lowered):
            return SanitizationResult(
                sanitized_text=normalized,
                suspicious=True,
                reason=f"Matched suspicious content pattern: {pattern}",
                confidence=0.0,
            )

    confidence = 1.0
    if "{{" in normalized or "}}" in normalized:
        confidence = 0.55

    reason = "Sanitized content approved"
    suspicious = confidence < settings.min_trust_confidence
    if suspicious:
        reason = "Content confidence below trust threshold"

    return SanitizationResult(sanitized_text=normalized, suspicious=suspicious, reason=reason, confidence=confidence)


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
