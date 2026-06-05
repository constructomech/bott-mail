"""Email parsing and HTML-to-text normalization.

Treats all email content as untrusted data. HTML is converted to plain text
with scripts/styles stripped. No content is ever executed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from bs4 import BeautifulSoup


@dataclass
class ParsedAttachment:
    filename: str | None
    content_type: str | None
    size: int | None


@dataclass
class ParsedMessage:
    message_id: str | None
    subject: str | None
    from_addr: str | None
    to_addrs: list[str]
    cc_addrs: list[str]
    date_utc: str | None
    body_text: str
    has_attachments: bool
    attachments: list[ParsedAttachment] = field(default_factory=list)
    raw_headers: str = ""


_WS_RE = re.compile(r"[ \t\f\v]+")
_MULTINL_RE = re.compile(r"\n{3,}")


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_RE.sub(" ", line).rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    return _MULTINL_RE.sub("\n\n", text).strip()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head", "title", "meta", "link"]):
        tag.decompose()
    # Preserve link targets inline so summaries retain useful URLs.
    for a in soup.find_all("a"):
        href = a.get("href")
        label = a.get_text(strip=True)
        if href and label and href not in label:
            a.replace_with(f"{label} ({href})")
    text = soup.get_text(separator="\n")
    return normalize_whitespace(text)


def _addr_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [addr for _, addr in getaddresses([value]) if addr]


def _first_addr(value: str | None) -> str | None:
    addrs = _addr_list(value)
    return addrs[0] if addrs else None


def _to_utc_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_body(msg: EmailMessage) -> str:
    """Prefer text/plain; fall back to converted HTML."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disp = (part.get_content_disposition() or "").lower()
            if disp == "attachment":
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plain_parts.append(_part_text(part))
            elif ctype == "text/html":
                html_parts.append(_part_text(part))
    else:
        ctype = msg.get_content_type()
        if ctype == "text/html":
            html_parts.append(_part_text(msg))
        else:
            plain_parts.append(_part_text(msg))

    if any(p.strip() for p in plain_parts):
        return normalize_whitespace("\n".join(plain_parts))
    if html_parts:
        return html_to_text("\n".join(html_parts))
    return ""


def _part_text(part: EmailMessage) -> str:
    try:
        payload = part.get_content()
        if isinstance(payload, bytes):
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return str(payload)
    except (LookupError, ValueError):
        raw = part.get_payload(decode=True)
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return ""


def _extract_attachments(msg: EmailMessage) -> list[ParsedAttachment]:
    out: list[ParsedAttachment] = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if disp == "attachment" or filename:
            payload = part.get_payload(decode=True)
            size = len(payload) if isinstance(payload, bytes) else None
            out.append(
                ParsedAttachment(
                    filename=filename,
                    content_type=part.get_content_type(),
                    size=size,
                )
            )
    return out


_KEEP_HEADERS = ("From", "To", "Cc", "Subject", "Date", "Message-ID")


def parse_email(raw_bytes: bytes) -> ParsedMessage:
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    attachments = _extract_attachments(msg)
    body_text = _extract_body(msg)

    raw_headers = "\n".join(
        f"{h}: {msg[h]}" for h in _KEEP_HEADERS if msg[h] is not None
    )

    return ParsedMessage(
        message_id=(msg["Message-ID"] or None),
        subject=(str(msg["Subject"]) if msg["Subject"] is not None else None),
        from_addr=_first_addr(msg["From"]),
        to_addrs=_addr_list(msg["To"]),
        cc_addrs=_addr_list(msg["Cc"]),
        date_utc=_to_utc_iso(msg["Date"]),
        body_text=body_text,
        has_attachments=bool(attachments),
        attachments=attachments,
        raw_headers=raw_headers,
    )


def make_snippet(body_text: str, limit: int = 200) -> str:
    snippet = normalize_whitespace(body_text).replace("\n", " ")
    if len(snippet) <= limit:
        return snippet
    return snippet[:limit].rstrip() + "…"
