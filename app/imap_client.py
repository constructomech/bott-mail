"""IMAP client for Proton Bridge (read-only).

Connects to Proton Bridge's localhost IMAP. Only performs read operations:
SELECT (readonly), SEARCH, FETCH. No STORE/COPY/MOVE/EXPUNGE are used.
"""
from __future__ import annotations

import imaplib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bott-mail.imap")


@dataclass
class FetchedMessage:
    uid: str
    folder: str
    raw_bytes: bytes
    unread: bool
    flagged: bool


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

    def _connect(self) -> imaplib.IMAP4:
        if self._tls:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(self._host, self._port)
        else:
            conn = imaplib.IMAP4(self._host, self._port)
        conn.login(self._username, self._password)
        return conn

    def check_connection(self) -> bool:
        try:
            conn = self._connect()
            try:
                conn.noop()
            finally:
                conn.logout()
            return True
        except Exception as exc:  # noqa: BLE001 - health check must not raise
            log.warning("IMAP connection check failed: %s", exc)
            return False

    def fetch_recent(self, folder: str, days: int) -> list[FetchedMessage]:
        """Fetch messages in `folder` newer than `days` days.

        Uses readonly SELECT so no flags are modified (reading does not mark
        messages as seen).
        """
        conn = self._connect()
        out: list[FetchedMessage] = []
        try:
            status, _ = conn.select(self._encode_folder(folder), readonly=True)
            if status != "OK":
                log.warning("Could not SELECT folder %s", folder)
                return out

            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
                "%d-%b-%Y"
            )
            status, data = conn.uid("SEARCH", None, f"(SINCE {since})")
            if status != "OK" or not data or not data[0]:
                return out

            uids = data[0].split()
            for uid in uids:
                uid_str = uid.decode()
                status, fetched = conn.uid(
                    "FETCH", uid_str, "(FLAGS RFC822)"
                )
                if status != "OK" or not fetched:
                    continue
                raw_bytes = b""
                flags_blob = b""
                for item in fetched:
                    if isinstance(item, tuple) and len(item) == 2:
                        flags_blob += item[0] or b""
                        raw_bytes = item[1] or b""
                    elif isinstance(item, (bytes, bytearray)):
                        flags_blob += bytes(item)
                if not raw_bytes:
                    continue
                flags_text = flags_blob.decode(errors="replace")
                unread = "\\Seen" not in flags_text
                flagged = "\\Flagged" in flags_text
                out.append(
                    FetchedMessage(
                        uid=uid_str,
                        folder=folder,
                        raw_bytes=raw_bytes,
                        unread=unread,
                        flagged=flagged,
                    )
                )
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
        return out

    @staticmethod
    def _encode_folder(folder: str) -> str:
        # Quote folder names that contain spaces (e.g. "All Mail").
        if " " in folder and not folder.startswith('"'):
            return f'"{folder}"'
        return folder
