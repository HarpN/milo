from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScrapeRequest(BaseModel):
    guide_url: str = Field(min_length=1, max_length=2048)
    game_title: str = Field(default="Unknown Game", min_length=1, max_length=200)
    platform: str = Field(default="PS5", min_length=1, max_length=32)
    commit: bool = Field(default=False)
    source: str = Field(default="web", min_length=1, max_length=64)
    quality_views: int = Field(default=0, ge=0)
    quality_age_days: int = Field(default=0, ge=0)


class GuideChunk(BaseModel):
    chunk_index: int
    heading: str
    text: str
    token_count: int
    trust_status: str = Field(default="approved", pattern="^(approved|rejected)$")
    trust_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_domain: str = Field(default="", min_length=0, max_length=255)
    content_hash: str = Field(default="", min_length=0, max_length=128)
    sanitizer_version: str = Field(default="", min_length=0, max_length=128)
    safety_notes: str = Field(default="", min_length=0, max_length=512)


class GuideDocument(BaseModel):
    guide_url: str
    game_title: str
    platform: str
    source: str
    fetched_at: str
    correlation_id: str
    summary: str
    quality_views: int = Field(default=0, ge=0)
    quality_age_days: int = Field(default=0, ge=0)
    quality_score: float = Field(default=0.0, ge=0.0)
    chunks: list[GuideChunk] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    trust_status: str = Field(default="approved", pattern="^(approved|rejected)$")
    trust_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_domain: str = Field(default="", min_length=0, max_length=255)
    sanitizer_version: str = Field(default="", min_length=0, max_length=128)


class JudyProposal(BaseModel):
    transaction_metadata: dict[str, str]
    proposed_action: dict[str, Any]
    agent_rationale: str
    guide_document: GuideDocument
