"""bott-mail-service — FastAPI app (Phase 1, read-only)."""
from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query

from . import __version__
from .audit import AuditLog
from .auth import TokenAuth, make_dependency
from .config import Settings
from .imap_client import ImapClient
from .index import MessageIndex
from .models import (
    HealthResponse,
    MessageDetail,
    MessageResponse,
    RecentResponse,
    SearchRequest,
    SearchResponse,
    SyncRequest,
    SyncResponse,
)
from .safety import assert_read_only, clamp_body, clamp_days, clamp_limit
from .sync import Syncer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("bott-mail")

settings = Settings.from_env()
assert_read_only(settings.safety)

auth = TokenAuth(settings.read_token)
require_token = make_dependency(auth)
audit = AuditLog(settings.storage.audit_log)
index = MessageIndex(settings.storage.sqlite_path, account=settings.account)
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


def _run_incremental(trigger: str) -> None:
    try:
        result = syncer.incremental(settings.sync.folders)
        _last_incremental["result"] = result
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
    log.info("bott-mail-service %s starting (read-only)", __version__)
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
async def sync(req: SyncRequest, token_id: str = Depends(require_token)) -> SyncResponse:
    days = clamp_days(req.days, settings.sync.default_days, settings.safety)
    folders = req.folders or settings.sync.folders
    result = syncer.sync(days, folders)
    audit.record(actor=_ACTOR, token_id=token_id, operation="sync", days=days, **result)
    return SyncResponse(**result)


@app.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest, token_id: str = Depends(require_token)
) -> SearchResponse:
    days = clamp_days(req.days, 90, settings.safety)
    limit = clamp_limit(req.limit, settings.safety)
    rows = index.search(req.query, days=days, limit=limit)
    audit.record(
        actor=_ACTOR,
        token_id=token_id,
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
    token_id: str = Depends(require_token),
) -> RecentResponse:
    days_c = clamp_days(days, settings.sync.default_days, settings.safety)
    limit_c = clamp_limit(limit, settings.safety)
    rows = index.recent(days=days_c, limit=limit_c, unread_only=unread)
    audit.record(
        actor=_ACTOR,
        token_id=token_id,
        operation="recent",
        days=days_c,
        unread_only=unread,
        result_count=len(rows),
    )
    return RecentResponse(results=rows)


@app.get("/messages/{message_id}", response_model=MessageResponse)
async def get_message(
    message_id: str, token_id: str = Depends(require_token)
) -> MessageResponse:
    row = index.get_message(message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")
    row["text"] = clamp_body(row.get("text") or "", settings.safety)
    audit.record(
        actor=_ACTOR,
        token_id=token_id,
        operation="message_read",
        message_id=message_id,
    )
    return MessageResponse(message=MessageDetail(**row))
