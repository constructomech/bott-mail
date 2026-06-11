"""Safety helpers and the central policy gate.

Phase 1 is strictly read-only. This module provides a single place to assert
that prohibited capabilities are disabled, and to clamp/validate request
parameters server-side regardless of what a caller asks for.
"""
from __future__ import annotations

from .config import SafetyConfig

# Capabilities that must NEVER be implemented in this service, regardless of
# config. Listed explicitly so any future code that references them is obviously
# wrong during review.
FORBIDDEN_CAPABILITIES = (
    "send",
    "reply",
    "forward",
    "draft",
    "delete",
    "empty_trash",
    "create_filter",
    "create_forwarding",
)


class PolicyError(Exception):
    """Raised when a request violates the configured safety policy."""


def assert_read_only(safety: SafetyConfig) -> None:
    """Verify forbidden capabilities are disabled. Called at startup."""
    if safety.allow_send or safety.allow_delete:
        raise PolicyError(
            "allow_send/allow_delete must be false; send and delete are never "
            "supported by bott-mail-service."
        )


def assert_archive_allowed(safety: SafetyConfig) -> None:
    if not safety.allow_archive:
        raise PolicyError("Archive is disabled by safety.allow_archive=false")
    if not safety.archive_folder.strip():
        raise PolicyError("Archive folder is not configured")


def clamp_limit(requested: int | None, safety: SafetyConfig) -> int:
    if requested is None or requested <= 0:
        return min(10, safety.max_results)
    return min(requested, safety.max_results)


def clamp_days(requested: int | None, default: int, safety: SafetyConfig) -> int:
    if requested is None or requested <= 0:
        requested = default
    return min(requested, safety.max_sync_days)


def clamp_body(text: str, safety: SafetyConfig) -> str:
    if len(text) <= safety.max_body_chars:
        return text
    return text[: safety.max_body_chars] + "\n\n[truncated]"
