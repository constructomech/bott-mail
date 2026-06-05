"""Configuration loading for bott-mail-service.

Config comes from a YAML file (path in BOTT_MAIL_CONFIG) plus environment
variables. The YAML references env var *names* for anything sensitive so that
secrets never live in the config file itself. Secrets (read token, Proton
Bridge IMAP credentials) are always read from the environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml


def _resolve_env_ref(cfg: dict, key: str, default_env: str) -> str:
    """Resolve a `<key>_env` config entry to the value of that env var.

    e.g. imap.host_env: PROTON_BRIDGE_IMAP_HOST -> os.environ[...]
    """
    env_name = cfg.get(key, default_env)
    return os.environ.get(env_name, "")


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class ImapConfig:
    host: str = "127.0.0.1"
    port: int = 1143
    username: str = ""
    password: str = ""
    tls: bool = False


@dataclass
class SyncConfig:
    default_days: int = 30
    folders: list[str] = field(default_factory=lambda: ["INBOX"])
    exclude_folders: list[str] = field(
        default_factory=lambda: ["Trash", "Spam", "Sent", "Drafts", "All Mail"]
    )
    interval_minutes: int = 0  # 0 disables the scheduled sync worker


@dataclass
class StorageConfig:
    sqlite_path: str = "/data/data/mail.sqlite"
    audit_log: str = "/data/data/audit.log"


@dataclass
class SafetyConfig:
    # Phase 1 is read-only. These flags exist so the service can *refuse*
    # write operations even if endpoints are added later by mistake.
    allow_send: bool = False
    allow_delete: bool = False
    allow_archive: bool = False
    allow_label: bool = False
    expose_raw_html: bool = False
    max_body_chars: int = 12000
    max_results: int = 25
    max_sync_days: int = 365


@dataclass
class Settings:
    server: ServerConfig
    imap: ImapConfig
    sync: SyncConfig
    storage: StorageConfig
    safety: SafetyConfig
    read_token: str
    account: str = "default"

    @classmethod
    def from_env(cls) -> "Settings":
        config_path = os.environ.get("BOTT_MAIL_CONFIG", "")
        raw: dict = {}
        if config_path and os.path.exists(config_path):
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}

        server_raw = raw.get("server", {}) or {}
        server = ServerConfig(
            host=server_raw.get("host", ServerConfig.host),
            port=int(server_raw.get("port", ServerConfig.port)),
        )

        auth_raw = raw.get("auth", {}) or {}
        read_token_env = auth_raw.get("read_token_env", "BOTT_MAIL_READ_TOKEN")
        read_token = os.environ.get(read_token_env, "")
        if not read_token:
            raise ValueError(
                f"Read token env var {read_token_env!r} is empty; "
                "set BOTT_MAIL_READ_TOKEN (or the configured env name)."
            )

        imap_raw = raw.get("imap", {}) or {}
        imap = ImapConfig(
            host=_resolve_env_ref(imap_raw, "host_env", "PROTON_BRIDGE_IMAP_HOST")
            or ImapConfig.host,
            port=int(
                _resolve_env_ref(imap_raw, "port_env", "PROTON_BRIDGE_IMAP_PORT")
                or ImapConfig.port
            ),
            username=_resolve_env_ref(
                imap_raw, "username_env", "PROTON_BRIDGE_IMAP_USERNAME"
            ),
            password=_resolve_env_ref(
                imap_raw, "password_env", "PROTON_BRIDGE_IMAP_PASSWORD"
            ),
            tls=bool(imap_raw.get("tls", ImapConfig.tls)),
        )

        sync_raw = raw.get("sync", {}) or {}
        sync = SyncConfig(
            default_days=int(sync_raw.get("default_days", SyncConfig.default_days)),
            folders=list(sync_raw.get("folders", ["INBOX"])),
            exclude_folders=list(
                sync_raw.get("exclude_folders", SyncConfig().exclude_folders)
            ),
            interval_minutes=int(
                sync_raw.get("interval_minutes", SyncConfig.interval_minutes)
            ),
        )

        storage_raw = raw.get("storage", {}) or {}
        storage = StorageConfig(
            sqlite_path=storage_raw.get("sqlite_path", StorageConfig.sqlite_path),
            audit_log=storage_raw.get("audit_log", StorageConfig.audit_log),
        )

        safety_raw = raw.get("safety", {}) or {}
        safety = SafetyConfig(
            allow_send=bool(safety_raw.get("allow_send", False)),
            allow_delete=bool(safety_raw.get("allow_delete", False)),
            allow_archive=bool(safety_raw.get("allow_archive", False)),
            allow_label=bool(safety_raw.get("allow_label", False)),
            expose_raw_html=bool(safety_raw.get("expose_raw_html", False)),
            max_body_chars=int(
                safety_raw.get("max_body_chars", SafetyConfig.max_body_chars)
            ),
            max_results=int(safety_raw.get("max_results", SafetyConfig.max_results)),
            max_sync_days=int(
                safety_raw.get("max_sync_days", SafetyConfig.max_sync_days)
            ),
        )

        return cls(
            server=server,
            imap=imap,
            sync=sync,
            storage=storage,
            safety=safety,
            read_token=read_token,
            account=raw.get("account", "default"),
        )
