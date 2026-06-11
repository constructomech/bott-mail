"""IMAP client for Proton Bridge (read-only) using IMAPClient.

Supports:
  - UID-based incremental fetch (only genuinely new messages)
  - lightweight flag-only refresh (read/unread, flagged) for a recent window
  - IMAP IDLE for near-instant new-mail notifications

Write operations are only added behind service-level policy gates. Archive uses
IMAP MOVE only; there is intentionally no copy+delete fallback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from imapclient import IMAPClient

log = logging.getLogger("bott-mail.imap")


@dataclass
class FetchedMessage:
    uid: int
    folder: str
    raw_bytes: bytes
    unread: bool
    flagged: bool


@dataclass
class FetchResult:
    uidvalidity: int
    messages: list[FetchedMessage]
    max_uid: int
    reset: bool  # True if UIDVALIDITY changed and a full re-window was done


def _since_date(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


class ImapClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        tls: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._tls = tls

    def connect(self) -> IMAPClient:
        client = IMAPClient(self._host, port=self._port, ssl=self._tls, use_uid=True)
        client.login(self._username, self._password)
        return client

    def check_connection(self) -> bool:
        try:
            client = self.connect()
            try:
                client.noop()
            finally:
                client.logout()
            return True
        except Exception as exc:  # noqa: BLE001 - health check must not raise
            log.warning("IMAP connection check failed: %s", exc)
            return False

    # -- fetching -------------------------------------------------------

    def fetch_new(
        self,
        folder: str,
        last_uid: int,
        known_uidvalidity: int | None,
        window_days: int,
    ) -> FetchResult:
        """Fetch messages newer than `last_uid`.

        On first run (last_uid <= 0) or on a UIDVALIDITY change, falls back to a
        date-bounded window of `window_days` so we never fetch the entire
        mailbox.
        """
        client = self.connect()
        try:
            info = client.select_folder(folder, readonly=True)
            uidvalidity = int(info.get(b"UIDVALIDITY", 0))
            reset = known_uidvalidity is not None and uidvalidity != known_uidvalidity

            if last_uid <= 0 or reset:
                since = _since_date(window_days)
                uids = client.search(["SINCE", since])
            else:
                uids = client.search(["UID", f"{last_uid + 1}:*"])
                # `N:*` can echo the highest UID even when none are new; filter.
                uids = [u for u in uids if u > last_uid]

            messages: list[FetchedMessage] = []
            max_uid = 0 if reset else last_uid
            if uids:
                resp = client.fetch(uids, ["FLAGS", "RFC822"])
                for uid, data in resp.items():
                    raw = data.get(b"RFC822")
                    if not raw:
                        continue
                    flags = data.get(b"FLAGS", ())
                    messages.append(
                        FetchedMessage(
                            uid=int(uid),
                            folder=folder,
                            raw_bytes=raw,
                            unread=b"\\Seen" not in flags,
                            flagged=b"\\Flagged" in flags,
                        )
                    )
                    max_uid = max(max_uid, int(uid))
            return FetchResult(
                uidvalidity=uidvalidity,
                messages=messages,
                max_uid=max_uid,
                reset=reset,
            )
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass

    def fetch_flags(self, folder: str, window_days: int) -> dict[int, tuple[bool, bool]]:
        """Return {uid: (unread, flagged)} for a recent window (no bodies)."""
        client = self.connect()
        try:
            client.select_folder(folder, readonly=True)
            uids = client.search(["SINCE", _since_date(window_days)])
            out: dict[int, tuple[bool, bool]] = {}
            if uids:
                resp = client.fetch(uids, ["FLAGS"])
                for uid, data in resp.items():
                    flags = data.get(b"FLAGS", ())
                    out[int(uid)] = (
                        b"\\Seen" not in flags,
                        b"\\Flagged" in flags,
                    )
            return out
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass

    # -- mutations ------------------------------------------------------

    def archive_message(self, folder: str, uid: str, archive_folder: str) -> None:
        """Move one message to the archive folder using UID MOVE only."""
        client = self.connect()
        try:
            client.select_folder(folder, readonly=False)
            client.move([int(uid)], archive_folder)
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass
