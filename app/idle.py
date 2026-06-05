"""IMAP IDLE watcher thread for near-instant new-mail notifications.

Holds its own long-lived IMAPClient connection on one folder (typically INBOX),
issues IDLE, and invokes a callback whenever the server reports activity. The
IDLE timeout also drives a periodic refresh (so read/unread changes propagate
even with no new mail), and keeps the connection alive within IMAP's ~29 min
IDLE limit.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .imap_client import ImapClient

log = logging.getLogger("bott-mail.idle")


class IdleWatcher(threading.Thread):
    def __init__(
        self,
        imap: ImapClient,
        folder: str,
        on_event: Callable[[], None],
        stop_event: threading.Event,
        refresh_seconds: int = 300,
    ) -> None:
        super().__init__(daemon=True, name=f"idle-{folder}")
        self._imap = imap
        self._folder = folder
        self._on_event = on_event
        self._stop = stop_event
        # IMAP requires re-issuing IDLE well under ~29 min; cap at 20 min.
        self._refresh = max(30, min(refresh_seconds, 20 * 60))

    def run(self) -> None:
        backoff = 5
        while not self._stop.is_set():
            client = None
            try:
                client = self._imap.connect()
                client.select_folder(self._folder, readonly=True)
                log.info("IDLE watching folder=%s (refresh=%ds)", self._folder, self._refresh)
                backoff = 5
                # Sync once on (re)connect to catch anything missed while down.
                self._fire()
                while not self._stop.is_set():
                    client.idle()
                    responses = client.idle_check(timeout=self._refresh)
                    client.idle_done()
                    if self._stop.is_set():
                        break
                    if responses:
                        log.info("IDLE activity on %s: %s", self._folder, responses)
                    # Fire on activity OR on timeout (periodic flag refresh).
                    self._fire()
            except Exception as exc:  # noqa: BLE001
                log.warning("IDLE watcher error on %s: %s (retry in %ds)", self._folder, exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 300)
            finally:
                if client is not None:
                    try:
                        client.logout()
                    except Exception:  # noqa: BLE001
                        pass
        log.info("IDLE watcher stopped for %s", self._folder)

    def _fire(self) -> None:
        try:
            self._on_event()
        except Exception as exc:  # noqa: BLE001
            log.warning("IDLE on_event handler failed: %s", exc)
