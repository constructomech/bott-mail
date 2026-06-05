"""SQLite-backed message index with FTS5 full-text search."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .mail_parse import ParsedMessage, make_snippet

_MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "migrations"
)


def stable_id(account: str, folder: str, uid: str) -> str:
    return hashlib.sha256(f"{account}\0{folder}\0{uid}".encode()).hexdigest()


@dataclass
class IndexResult:
    inserted: int
    updated: int
    seen: int


class MessageIndex:
    def __init__(self, sqlite_path: str, account: str = "default") -> None:
        self._path = sqlite_path
        self._account = account
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            for path in sorted(glob.glob(os.path.join(_MIGRATIONS_DIR, "*.sql"))):
                with open(path) as f:
                    conn.executescript(f.read())

    # ---- writes -------------------------------------------------------

    def upsert_messages(
        self, folder: str, parsed: list[tuple[str, ParsedMessage, bool, bool]]
    ) -> IndexResult:
        """Insert or update messages.

        Each item: (uid, ParsedMessage, unread, flagged)
        """
        inserted = 0
        updated = 0
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock, self._connect() as conn:
            for uid, msg, unread, flagged in parsed:
                uid = str(uid)
                mid = stable_id(self._account, folder, uid)
                body_sha = hashlib.sha256(msg.body_text.encode()).hexdigest()
                snippet = make_snippet(msg.body_text)
                attachments_json = json.dumps(
                    [
                        {
                            "filename": a.filename,
                            "content_type": a.content_type,
                            "size": a.size,
                        }
                        for a in msg.attachments
                    ]
                )
                existing = conn.execute(
                    "SELECT rowid, body_sha256 FROM messages WHERE id = ?", (mid,)
                ).fetchone()

                row = (
                    mid,
                    self._account,
                    folder,
                    uid,
                    msg.message_id,
                    msg.message_id,  # thread_key placeholder (Phase 3 improves)
                    msg.subject,
                    msg.from_addr,
                    json.dumps(msg.to_addrs),
                    json.dumps(msg.cc_addrs),
                    msg.date_utc,
                    1 if unread else 0,
                    1 if flagged else 0,
                    1 if msg.has_attachments else 0,
                    snippet,
                    msg.body_text,
                    body_sha,
                    msg.raw_headers,
                    attachments_json,
                    now,
                )

                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO messages (
                          id, account, folder, uid, message_id, thread_key,
                          subject, from_addr, to_addrs, cc_addrs, date_utc,
                          unread, flagged, has_attachments, snippet, body_text,
                          body_sha256, raw_headers, attachments_json, synced_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        row,
                    )
                    new_rowid = conn.execute(
                        "SELECT rowid FROM messages WHERE id = ?", (mid,)
                    ).fetchone()[0]
                    conn.execute(
                        "INSERT INTO messages_fts(rowid, subject, from_addr, body_text)"
                        " VALUES (?,?,?,?)",
                        (new_rowid, msg.subject or "", msg.from_addr or "", msg.body_text),
                    )
                    inserted += 1
                else:
                    conn.execute(
                        """
                        UPDATE messages SET
                          message_id=?, thread_key=?, subject=?, from_addr=?,
                          to_addrs=?, cc_addrs=?, date_utc=?, unread=?, flagged=?,
                          has_attachments=?, snippet=?, body_text=?, body_sha256=?,
                          raw_headers=?, attachments_json=?, synced_at=?
                        WHERE id=?
                        """,
                        (
                            msg.message_id,
                            msg.message_id,
                            msg.subject,
                            msg.from_addr,
                            json.dumps(msg.to_addrs),
                            json.dumps(msg.cc_addrs),
                            msg.date_utc,
                            1 if unread else 0,
                            1 if flagged else 0,
                            1 if msg.has_attachments else 0,
                            snippet,
                            msg.body_text,
                            body_sha,
                            msg.raw_headers,
                            attachments_json,
                            now,
                            mid,
                        ),
                    )
                    conn.execute(
                        "UPDATE messages_fts SET subject=?, from_addr=?, body_text=?"
                        " WHERE rowid=?",
                        (
                            msg.subject or "",
                            msg.from_addr or "",
                            msg.body_text,
                            existing["rowid"],
                        ),
                    )
                    updated += 1
        return IndexResult(inserted=inserted, updated=updated, seen=len(parsed))

    # ---- reads --------------------------------------------------------

    def search(self, query: str, days: int, limit: int) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        fts_query = _to_fts_query(query)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.* FROM messages_fts f
                JOIN messages m ON m.rowid = f.rowid
                WHERE messages_fts MATCH ?
                  AND (m.date_utc IS NULL OR m.date_utc >= ?)
                ORDER BY m.date_utc DESC
                LIMIT ?
                """,
                (fts_query, since, limit),
            ).fetchall()
        return [self._row_to_summary(r) for r in rows]

    def recent(self, days: int, limit: int, unread_only: bool) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        sql = (
            "SELECT * FROM messages WHERE (date_utc IS NULL OR date_utc >= ?)"
        )
        params: list = [since]
        if unread_only:
            sql += " AND unread = 1"
        sql += " ORDER BY date_utc DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_summary(r) for r in rows]

    def get_message(self, message_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_detail(row)

    # ---- folder sync state -------------------------------------------

    def get_folder_state(self, folder: str) -> tuple[int | None, int]:
        """Return (uidvalidity, last_uid) for a folder; defaults (None, 0)."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT uidvalidity, last_uid FROM folder_state"
                " WHERE account = ? AND folder = ?",
                (self._account, folder),
            ).fetchone()
        if row is None:
            return (None, 0)
        return (row["uidvalidity"], row["last_uid"])

    def set_folder_state(self, folder: str, uidvalidity: int, last_uid: int) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO folder_state (account, folder, uidvalidity, last_uid, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(account, folder) DO UPDATE SET
                  uidvalidity=excluded.uidvalidity,
                  last_uid=excluded.last_uid,
                  updated_at=excluded.updated_at
                """,
                (self._account, folder, uidvalidity, last_uid, now),
            )

    def update_flags(
        self, folder: str, flags: dict[int, tuple[bool, bool]]
    ) -> int:
        """Update unread/flagged for existing messages by uid. Returns #changed."""
        if not flags:
            return 0
        changed = 0
        with self._lock, self._connect() as conn:
            for uid, (unread, flagged) in flags.items():
                cur = conn.execute(
                    "UPDATE messages SET unread=?, flagged=?"
                    " WHERE account=? AND folder=? AND uid=?"
                    " AND (unread<>? OR flagged<>?)",
                    (
                        1 if unread else 0,
                        1 if flagged else 0,
                        self._account,
                        folder,
                        str(uid),
                        1 if unread else 0,
                        1 if flagged else 0,
                    ),
                )
                changed += cur.rowcount
        return changed

    # ---- helpers ------------------------------------------------------

    @staticmethod
    def _row_to_summary(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "folder": r["folder"],
            "from": r["from_addr"],
            "to": json.loads(r["to_addrs"] or "[]"),
            "subject": r["subject"],
            "date": r["date_utc"],
            "snippet": r["snippet"],
            "unread": bool(r["unread"]),
            "has_attachments": bool(r["has_attachments"]),
        }

    @staticmethod
    def _row_to_detail(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "folder": r["folder"],
            "from": r["from_addr"],
            "to": json.loads(r["to_addrs"] or "[]"),
            "cc": json.loads(r["cc_addrs"] or "[]"),
            "subject": r["subject"],
            "date": r["date_utc"],
            "text": r["body_text"],
            "unread": bool(r["unread"]),
            "attachments": json.loads(r["attachments_json"] or "[]"),
        }


def _to_fts_query(query: str) -> str:
    """Turn a free-text query into a safe FTS5 MATCH expression.

    Each whitespace-separated term is quoted to avoid FTS5 syntax errors and
    treated as a required term.
    """
    terms = [t for t in query.replace('"', " ").split() if t]
    if not terms:
        return '""'
    return " ".join(f'"{t}"' for t in terms)
