from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScrapeRequest(BaseModel):
    guide_url: str = Field(min_length=1, max_length=2048)
    game_title: str = Field(default="Unknown Game", min_length=1, max_length=200)
    commit: bool = Field(default=False)
    source: str = Field(default="web", min_length=1, max_length=64)


class GuideChunk(BaseModel):
    chunk_index: int
    heading: str
    text: str
    token_count: int


class GuideDocument(BaseModel):
    guide_url: str
    game_title: str
    source: str
    fetched_at: str
    correlation_id: str
    summary: str
    chunks: list[GuideChunk] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class JudyProposal(BaseModel):
    transaction_metadata: dict[str, str]
    proposed_action: dict[str, Any]
    agent_rationale: str
    guide_document: GuideDocument
