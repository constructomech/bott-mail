"""Pydantic request/response models for the bott-mail-service API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "bott-mail-service"
    mode: str = "read-only"
    version: str
    imap_connected: bool | None = None


class SyncRequest(BaseModel):
    days: int | None = Field(default=None, ge=1)
    folders: list[str] | None = None


class SyncResponse(BaseModel):
    ok: bool = True
    folders: list[str]
    messages_seen: int
    messages_inserted: int
    messages_updated: int


class SearchRequest(BaseModel):
    query: str
    days: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1)


class Attachment(BaseModel):
    filename: str | None = None
    content_type: str | None = None
    size: int | None = None


class MessageSummary(BaseModel):
    id: str
    folder: str
    from_addr: str | None = Field(default=None, alias="from")
    to: list[str] = Field(default_factory=list)
    subject: str | None = None
    date: str | None = None
    snippet: str | None = None
    unread: bool = False
    has_attachments: bool = False

    model_config = {"populate_by_name": True}


class SearchResponse(BaseModel):
    ok: bool = True
    query: str
    results: list[MessageSummary]


class RecentResponse(BaseModel):
    ok: bool = True
    results: list[MessageSummary]


class MessageDetail(BaseModel):
    id: str
    folder: str
    from_addr: str | None = Field(default=None, alias="from")
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str | None = None
    date: str | None = None
    text: str | None = None
    unread: bool = False
    attachments: list[Attachment] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class MessageResponse(BaseModel):
    ok: bool = True
    message: MessageDetail


class ArchiveResponse(BaseModel):
    ok: bool = True
    message_id: str
    from_folder: str
    archive_folder: str


class AutomationAction(BaseModel):
    type: str
    tag: str | None = None
    mechanism: str | None = None
    channel: str | None = None
    text: str | None = None


class AutomationRecommendationRequest(BaseModel):
    message_id: str
    rule_name: str | None = None
    classification: str
    confidence: float
    actions: list[AutomationAction] = Field(default_factory=list)
    reason: str | None = None


class BatchAutomationRecommendationItem(AutomationRecommendationRequest):
    request_id: str


class BatchAutomationRecommendationRequest(BaseModel):
    recommendations: list[BatchAutomationRecommendationItem] = Field(min_length=1)


class AutomationRecommendationResponse(BaseModel):
    ok: bool = True
    decision_id: str
    message_id: str
    accepted: bool
    rejected_reasons: list[str] = Field(default_factory=list)
    dry_run: bool = True
    created_at: str


class AutomationBatchRecommendationResponse(BaseModel):
    ok: bool = True
    batch_id: str
    accepted_count: int
    rejected_count: int
    decisions: list[AutomationRecommendationResponse]
