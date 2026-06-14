"""Sync orchestration: pull from IMAP (window or incremental), parse, index."""
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

    def _target_folders(self, folders: list[str]) -> list[str]:
        excluded = {f.lower() for f in self._settings.sync.exclude_folders}
        return [f for f in folders if f.lower() not in excluded]

    def _ingest(self, folder: str, fetched) -> tuple[int, int, int, int, list[str]]:
        """Parse + upsert a batch.

        Returns (seen, inserted, updated, max_uid, inserted_ids).
        """
        parsed = []
        max_uid = 0
        for fm in fetched:
            max_uid = max(max_uid, fm.uid)
            try:
                pm = parse_email(fm.raw_bytes)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to parse uid=%s: %s", fm.uid, exc)
                continue
            parsed.append((fm.uid, pm, fm.unread, fm.flagged))
        result = self._index.upsert_messages(folder, parsed)
        return result.seen, result.inserted, result.updated, max_uid, result.inserted_ids

    def sync(self, days: int, folders: list[str]) -> dict:
        """Date-windowed sync (used by POST /sync). Updates folder_state."""
        seen = inserted = updated = 0
        inserted_ids: list[str] = []
        synced_folders: list[str] = []
        for folder in self._target_folders(folders):
            synced_folders.append(folder)
            known_uidvalidity, _ = self._index.get_folder_state(folder)
            res = self._imap.fetch_new(
                folder, last_uid=0, known_uidvalidity=known_uidvalidity, window_days=days
            )
            s, i, u, max_uid, ids = self._ingest(folder, res.messages)
            seen += s
            inserted += i
            updated += u
            inserted_ids.extend(ids)
            # Advance last_uid so subsequent incremental syncs are cheap.
            prev_uidvalidity, prev_last = self._index.get_folder_state(folder)
            new_last = max(prev_last, max_uid)
            self._index.set_folder_state(folder, res.uidvalidity, new_last)
            log.info(
                "Window sync folder=%s seen=%d inserted=%d updated=%d last_uid=%d",
                folder, s, i, u, new_last,
            )
        return {
            "folders": synced_folders,
            "messages_seen": seen,
            "messages_inserted": inserted,
            "messages_updated": updated,
            "inserted_ids": inserted_ids,
        }

    def incremental(self, folders: list[str]) -> dict:
        """UID-incremental sync + flag refresh (used by the IDLE/poll worker)."""
        window = self._settings.sync.default_days
        seen = inserted = updated = flag_changes = 0
        inserted_ids: list[str] = []
        synced_folders: list[str] = []
        for folder in self._target_folders(folders):
            synced_folders.append(folder)
            known_uidvalidity, last_uid = self._index.get_folder_state(folder)
            res = self._imap.fetch_new(
                folder, last_uid=last_uid,
                known_uidvalidity=known_uidvalidity, window_days=window,
            )
            s, i, u, max_uid, ids = self._ingest(folder, res.messages)
            seen += s
            inserted += i
            updated += u
            inserted_ids.extend(ids)
            new_last = max(last_uid if not res.reset else 0, max_uid)
            self._index.set_folder_state(folder, res.uidvalidity, new_last)

            # Refresh read/unread + flagged for the recent window (cheap, no bodies).
            try:
                flags = self._imap.fetch_flags(folder, window)
                flag_changes += self._index.update_flags(folder, flags)
            except Exception as exc:  # noqa: BLE001
                log.warning("Flag refresh failed for %s: %s", folder, exc)

            if i or u or s:
                log.info(
                    "Incremental folder=%s new=%d updated=%d last_uid=%d",
                    folder, i, u, new_last,
                )
        return {
            "folders": synced_folders,
            "messages_seen": seen,
            "messages_inserted": inserted,
            "messages_updated": updated,
            "flag_changes": flag_changes,
            "inserted_ids": inserted_ids,
        }
