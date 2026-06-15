"""bott-mail-service FastAPI app."""
from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query

from . import __version__
from .audit import AuditLog
from .auth import AuthContext, TokenAuth, TokenSpec, require_scope
from .automation import AutomationStore, ValidationResult, validate_recommendation
from .config import Settings
from .hermes import HermesWebhookClient, HermesWebhookConfig, HermesWebhookError
from .imap_client import ImapClient
from .index import MessageIndex
from .models import (
    HealthResponse,
    ArchiveResponse,
    AutomationBatchRecommendationResponse,
    AutomationRecommendationResponse,
    BatchAutomationRecommendationRequest,
    MessageDetail,
    MessageResponse,
    RecentResponse,
    SearchRequest,
    SearchResponse,
    SyncRequest,
    SyncResponse,
)
from .safety import (
    PolicyError,
    assert_archive_allowed,
    assert_label_allowed,
    assert_read_only,
    clamp_body,
    clamp_days,
    clamp_limit,
)
from .sync import Syncer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("bott-mail")

settings = Settings.from_env()
assert_read_only(settings.safety)

auth = TokenAuth(
    [
        TokenSpec(
            token_id=t.token_id,
            token=t.token,
            scopes=frozenset(t.scopes),
        )
        for t in settings.auth_tokens
    ]
)
require_sync = require_scope(auth, "sync:run")
require_search = require_scope(auth, "messages:search")
require_read = require_scope(auth, "messages:read")
require_archive = require_scope(auth, "messages:archive")
require_recommend = require_scope(auth, "automation:recommend")
audit = AuditLog(settings.storage.audit_log)
index = MessageIndex(settings.storage.sqlite_path, account=settings.account)
automation_store = AutomationStore(settings.storage.sqlite_path, account=settings.account)
hermes_webhook = HermesWebhookClient(
    HermesWebhookConfig(
        url=settings.hermes.webhook_url,
        secret=settings.hermes.webhook_secret,
        timeout_seconds=settings.hermes.webhook_timeout_seconds,
    )
)
imap = ImapClient(
    host=settings.imap.host,
    port=settings.imap.port,
    username=settings.imap.username,
    password=settings.imap.password,
    tls=settings.imap.tls,
)
syncer = Syncer(settings, imap, index)

_ACTOR = "hermes"
_worker_stop = threading.Event()
_last_incremental: dict[str, object] = {"result": None, "at": None}


def _auto_classify_inserted(message_ids: list[str], trigger: str) -> None:
    if not settings.automation.auto_classify_new_mail:
        return
    batch_size = max(0, settings.automation.auto_classify_batch_size)
    if batch_size <= 0:
        return
    messages = []
    for message_id in message_ids:
        message = index.get_message(message_id)
        if message is None:
            continue
        message["text"] = clamp_body(message.get("text") or "", settings.safety)
        messages.append(message)
    for start in range(0, len(messages), batch_size):
        try:
            _send_classification_batch(
                messages[start : start + batch_size],
                trigger=trigger,
                actor="worker",
                token_id="internal",
            )
        except HermesWebhookError as exc:
            log.warning("Auto-classify failed: %s", exc)
            continue


def _send_classification_batch(
    messages: list[dict], *, trigger: str, actor: str, token_id: str
) -> tuple[dict, int]:
    batch = automation_store.create_classification_batch(
        trigger=trigger,
        messages=messages,
    )
    try:
        status = hermes_webhook.send_classification_batch(
            batch=batch,
            messages=messages,
            callback_base_url=settings.automation.callback_base_url,
        )
    except HermesWebhookError as exc:
        audit.record(
            actor=actor,
            token_id=token_id,
            operation="automation_classification_failed",
            trigger=trigger,
            batch_id=batch["batch_id"],
            message_count=len(messages),
            error=type(exc).__name__,
        )
        raise

    audit.record(
        actor=actor,
        token_id=token_id,
        operation="automation_classification_requested",
        trigger=trigger,
        batch_id=batch["batch_id"],
        message_count=len(messages),
        webhook_status=status,
    )
    return batch, status


def _execute_recommendation_actions(
    *,
    decision_id: str,
    batch_id: str,
    request_id: str,
    message_id: str,
    actions: list[dict],
) -> None:
    if not settings.automation.execute_recommendations:
        return
    allowed_actions = set(settings.automation.autonomous_actions)
    for action in actions:
        action_type = str(action.get("type") or "")
        if action_type not in allowed_actions:
            automation_store.record_execution(
                decision_id=decision_id,
                batch_id=batch_id,
                request_id=request_id,
                message_id=message_id,
                action_type=action_type,
                status="skipped",
                detail=f"action not enabled for autonomous execution: {action}",
            )
            audit.record(
                actor="automation",
                token_id="internal",
                operation="automation_action_skipped",
                batch_id=batch_id,
                request_id=request_id,
                message_id=message_id,
                action_type=action_type,
                action=action,
                reason="action not enabled for autonomous execution",
            )
            continue

        row = index.get_message_locator(message_id)
        if row is None:
            automation_store.record_execution(
                decision_id=decision_id,
                batch_id=batch_id,
                request_id=request_id,
                message_id=message_id,
                action_type=action_type,
                status="skipped",
                detail="message no longer exists in index",
            )
            audit.record(
                actor="automation",
                token_id="internal",
                operation="automation_action_skipped",
                batch_id=batch_id,
                request_id=request_id,
                message_id=message_id,
                action_type=action_type,
                action=action,
                reason="message no longer exists in index",
            )
            continue

        try:
            if action_type == "add_tag":
                assert_label_allowed(settings.safety)
                tag = str(action.get("tag") or "").strip()
                if not tag:
                    raise PolicyError("add_tag action requires a tag")
                imap.add_tag_message(row["folder"], row["uid"], tag)
                automation_store.record_execution(
                    decision_id=decision_id,
                    batch_id=batch_id,
                    request_id=request_id,
                    message_id=message_id,
                    action_type=action_type,
                    status="executed",
                    detail=tag,
                )
                audit.record(
                    actor="automation",
                    token_id="internal",
                    operation="automation_action_executed",
                    batch_id=batch_id,
                    request_id=request_id,
                    message_id=message_id,
                    action_type=action_type,
                    tag=tag,
                    action=action,
                )
            elif action_type == "archive":
                assert_archive_allowed(settings.safety)
                archive_folder = settings.safety.archive_folder
                if row["folder"] != archive_folder:
                    imap.archive_message(row["folder"], row["uid"], archive_folder)
                    index.remove_message(message_id)
                automation_store.record_execution(
                    decision_id=decision_id,
                    batch_id=batch_id,
                    request_id=request_id,
                    message_id=message_id,
                    action_type=action_type,
                    status="executed",
                    detail=archive_folder,
                )
                audit.record(
                    actor="automation",
                    token_id="internal",
                    operation="automation_action_executed",
                    batch_id=batch_id,
                    request_id=request_id,
                    message_id=message_id,
                    action_type=action_type,
                    archive_folder=archive_folder,
                    action=action,
                )
            else:
                automation_store.record_execution(
                    decision_id=decision_id,
                    batch_id=batch_id,
                    request_id=request_id,
                    message_id=message_id,
                    action_type=action_type,
                    status="skipped",
                    detail=f"action type is not executable: {action}",
                )
                audit.record(
                    actor="automation",
                    token_id="internal",
                    operation="automation_action_skipped",
                    batch_id=batch_id,
                    request_id=request_id,
                    message_id=message_id,
                    action_type=action_type,
                    action=action,
                    reason="action type is not executable",
                )
        except Exception as exc:  # noqa: BLE001
            automation_store.record_execution(
                decision_id=decision_id,
                batch_id=batch_id,
                request_id=request_id,
                message_id=message_id,
                action_type=action_type,
                status="failed",
                detail=type(exc).__name__,
            )
            audit.record(
                actor="automation",
                token_id="internal",
                operation="automation_action_failed",
                batch_id=batch_id,
                request_id=request_id,
                message_id=message_id,
                action_type=action_type,
                error=type(exc).__name__,
                action=action,
            )


def _run_incremental(trigger: str) -> None:
    try:
        result = syncer.incremental(settings.sync.folders)
        _last_incremental["result"] = result
        _auto_classify_inserted(list(result.get("inserted_ids") or []), trigger)
        audit.record(
            actor="worker",
            token_id="internal",
            operation="incremental_sync",
            trigger=trigger,
            **result,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Incremental sync (%s) failed: %s", trigger, exc)


def _poll_loop() -> None:
    interval = settings.sync.interval_minutes * 60
    log.info("Poll sync worker enabled: every %d min", settings.sync.interval_minutes)
    while not _worker_stop.wait(interval):
        _run_incremental("poll")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("bott-mail-service %s starting", __version__)
    watchers = []
    if settings.sync.interval_minutes > 0:
        if settings.sync.mode == "idle":
            from .idle import IdleWatcher

            refresh = settings.sync.interval_minutes * 60
            for folder in settings.sync.idle_folders:
                w = IdleWatcher(
                    imap, folder,
                    on_event=lambda: _run_incremental("idle"),
                    stop_event=_worker_stop,
                    refresh_seconds=refresh,
                )
                w.start()
                watchers.append(w)
            log.info(
                "IDLE sync enabled on %s (refresh every %d min)",
                settings.sync.idle_folders, settings.sync.interval_minutes,
            )
        else:
            threading.Thread(target=_poll_loop, daemon=True).start()
    else:
        log.info("Background sync worker disabled (interval_minutes=0)")
    yield
    _worker_stop.set()
    log.info("bott-mail-service stopping")


app = FastAPI(title="bott-mail-service", version=__version__, lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        version=__version__,
        imap_connected=imap.check_connection(),
    )


@app.post("/sync", response_model=SyncResponse)
async def sync(req: SyncRequest, auth_ctx: AuthContext = Depends(require_sync)) -> SyncResponse:
    days = clamp_days(req.days, settings.sync.default_days, settings.safety)
    folders = req.folders or settings.sync.folders
    result = syncer.sync(days, folders)
    audit.record(actor=_ACTOR, token_id=auth_ctx.token_id, operation="sync", days=days, **result)
    return SyncResponse(**result)


@app.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest, auth_ctx: AuthContext = Depends(require_search)
) -> SearchResponse:
    days = clamp_days(req.days, 90, settings.safety)
    limit = clamp_limit(req.limit, settings.safety)
    rows = index.search(req.query, days=days, limit=limit)
    audit.record(
        actor=_ACTOR,
        token_id=auth_ctx.token_id,
        operation="search",
        query=req.query,
        result_count=len(rows),
    )
    return SearchResponse(query=req.query, results=rows)


@app.get("/recent", response_model=RecentResponse)
async def recent(
    days: int = Query(default=1, ge=1),
    unread: bool = Query(default=False),
    limit: int = Query(default=10, ge=1),
    auth_ctx: AuthContext = Depends(require_search),
) -> RecentResponse:
    days_c = clamp_days(days, settings.sync.default_days, settings.safety)
    limit_c = clamp_limit(limit, settings.safety)
    rows = index.recent(days=days_c, limit=limit_c, unread_only=unread)
    audit.record(
        actor=_ACTOR,
        token_id=auth_ctx.token_id,
        operation="recent",
        days=days_c,
        unread_only=unread,
        result_count=len(rows),
    )
    return RecentResponse(results=rows)


@app.get("/messages/{message_id}", response_model=MessageResponse)
async def get_message(
    message_id: str, auth_ctx: AuthContext = Depends(require_read)
) -> MessageResponse:
    row = index.get_message(message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")
    row["text"] = clamp_body(row.get("text") or "", settings.safety)
    audit.record(
        actor=_ACTOR,
        token_id=auth_ctx.token_id,
        operation="message_read",
        message_id=message_id,
    )
    return MessageResponse(message=MessageDetail(**row))


@app.post("/messages/{message_id}/archive", response_model=ArchiveResponse)
async def archive_message(
    message_id: str, auth_ctx: AuthContext = Depends(require_archive)
) -> ArchiveResponse:
    try:
        assert_archive_allowed(settings.safety)
    except PolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    row = index.get_message_locator(message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")

    archive_folder = settings.safety.archive_folder
    from_folder = row["folder"]
    if from_folder == archive_folder:
        audit.record(
            actor=_ACTOR,
            token_id=auth_ctx.token_id,
            operation="archive_noop",
            message_id=message_id,
            folder=from_folder,
        )
        return ArchiveResponse(
            message_id=message_id,
            from_folder=from_folder,
            archive_folder=archive_folder,
        )

    try:
        imap.archive_message(from_folder, row["uid"], archive_folder)
    except Exception as exc:  # noqa: BLE001
        audit.record(
            actor=_ACTOR,
            token_id=auth_ctx.token_id,
            operation="archive_failed",
            message_id=message_id,
            from_folder=from_folder,
            archive_folder=archive_folder,
            error=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="IMAP archive failed") from exc

    index.remove_message(message_id)
    audit.record(
        actor=_ACTOR,
        token_id=auth_ctx.token_id,
        operation="archive",
        message_id=message_id,
        from_folder=from_folder,
        archive_folder=archive_folder,
    )
    return ArchiveResponse(
        message_id=message_id,
        from_folder=from_folder,
        archive_folder=archive_folder,
    )


@app.post(
    "/automation/classification-batches/{batch_id}/recommendations",
    response_model=AutomationBatchRecommendationResponse,
)
async def record_classification_batch_recommendations(
    batch_id: str,
    req: BatchAutomationRecommendationRequest,
    auth_ctx: AuthContext = Depends(require_recommend),
) -> AutomationBatchRecommendationResponse:
    pending = automation_store.get_pending_batch_items(batch_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Classification batch not pending")

    request_ids = [item.request_id for item in req.recommendations]
    if len(request_ids) != len(set(request_ids)):
        raise HTTPException(status_code=400, detail="Duplicate request_id in recommendations")

    unknown = sorted(set(request_ids) - set(pending))
    if unknown:
        raise HTTPException(status_code=400, detail={"unknown_request_ids": unknown})

    decisions = []
    completed_request_ids = []
    for item in req.recommendations:
        expected = pending[item.request_id]
        message_id = expected["message_id"]
        row = index.get_message(message_id)
        extra_reasons = []
        if row is None:
            extra_reasons.append("message no longer exists")
        elif row.get("body_sha256") != expected["body_sha256"]:
            extra_reasons.append("message body changed since classification request")

        raw = item.model_dump()
        actions = [a.model_dump(exclude_none=True) for a in item.actions]
        validation = validate_recommendation(
            message_id=message_id,
            recommendation_message_id=item.message_id,
            classification=item.classification,
            confidence=item.confidence,
            actions=actions,
            safety=settings.safety,
        )
        if extra_reasons:
            validation = ValidationResult(
                accepted=False,
                rejected_reasons=validation.rejected_reasons + extra_reasons,
            )

        result = automation_store.record_decision(
            message_id=message_id,
            rule_name=item.rule_name,
            classification=item.classification,
            confidence=item.confidence,
            actions=actions,
            raw_recommendation=raw,
            validation=validation,
        )
        decisions.append(AutomationRecommendationResponse(**result))
        if validation.accepted:
            _execute_recommendation_actions(
                decision_id=result["decision_id"],
                batch_id=batch_id,
                request_id=item.request_id,
                message_id=message_id,
                actions=actions,
            )
        completed_request_ids.append(item.request_id)
        audit.record(
            actor=_ACTOR,
            token_id=auth_ctx.token_id,
            operation="automation_recommendation_dry_run",
            batch_id=batch_id,
            request_id=item.request_id,
            message_id=message_id,
            accepted=validation.accepted,
            rejected_reasons=validation.rejected_reasons,
            action_count=len(actions),
            actions=actions,
        )

    automation_store.complete_batch_items(batch_id, completed_request_ids)
    accepted_count = sum(1 for decision in decisions if decision.accepted)
    return AutomationBatchRecommendationResponse(
        batch_id=batch_id,
        accepted_count=accepted_count,
        rejected_count=len(decisions) - accepted_count,
        decisions=decisions,
    )
