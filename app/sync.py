"""Sync orchestration: pull from IMAP, parse, index."""
from __future__ import annotations

import logging

from .config import Settings
from .imap_client import ImapClient
from .index import MessageIndex
from .mail_parse import parse_email

log = logging.getLogger("bott-mail.sync")


class Syncer:
    def __init__(
        self, settings: Settings, imap: ImapClient, index: MessageIndex
    ) -> None:
        self._settings = settings
        self._imap = imap
        self._index = index

    def sync(self, days: int, folders: list[str]) -> dict:
        excluded = {f.lower() for f in self._settings.sync.exclude_folders}
        seen = inserted = updated = 0
        synced_folders: list[str] = []

        for folder in folders:
            if folder.lower() in excluded:
                log.info("Skipping excluded folder %s", folder)
                continue
            synced_folders.append(folder)
            fetched = self._imap.fetch_recent(folder, days)
            parsed = []
            for fm in fetched:
                try:
                    pm = parse_email(fm.raw_bytes)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to parse message uid=%s: %s", fm.uid, exc)
                    continue
                parsed.append((fm.uid, pm, fm.unread, fm.flagged))

            result = self._index.upsert_messages(folder, parsed)
            seen += result.seen
            inserted += result.inserted
            updated += result.updated
            log.info(
                "Synced folder=%s seen=%d inserted=%d updated=%d",
                folder,
                result.seen,
                result.inserted,
                result.updated,
            )

        return {
            "folders": synced_folders,
            "messages_seen": seen,
            "messages_inserted": inserted,
            "messages_updated": updated,
        }
