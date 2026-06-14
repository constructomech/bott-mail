"""Configuration loading for bott-mail-service.

Config comes from a YAML file (path in BOTT_MAIL_CONFIG) plus environment
variables. The YAML references env var *names* for anything sensitive so that
secrets never live in the config file itself. Secrets (read token, Proton
Bridge IMAP credentials) are always read from the environment.
"""
from __future__ import annotations

import logging
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
    interval_minutes: int = 0  # poll/refresh cadence; 0 disables the worker
    mode: str = "idle"  # "idle" (push + periodic refresh) or "poll"
    idle_folders: list[str] = field(default_factory=lambda: ["INBOX"])


@dataclass
class StorageConfig:
    sqlite_path: str = "/data/data/mail.sqlite"
    audit_log: str = "/data/data/audit.log"


@dataclass
class AuthTokenConfig:
    token_id: str
    token: str
    scopes: list[str]


@dataclass
class HermesConfig:
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_timeout_seconds: float = 10.0


@dataclass
class AutomationConfig:
    auto_classify_new_mail: bool = False
    auto_classify_limit_per_sync: int = 10


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
    archive_folder: str = "Archive"


@dataclass
class Settings:
    server: ServerConfig
    imap: ImapConfig
    sync: SyncConfig
    storage: StorageConfig
    safety: SafetyConfig
    hermes: HermesConfig
    automation: AutomationConfig
    auth_tokens: list[AuthTokenConfig]
    account: str = "default"

    @classmethod
    def from_env(cls) -> "Settings":
        config_path = os.environ.get("BOTT_MAIL_CONFIG", "")
        raw: dict = {}
        if config_path:
            if os.path.exists(config_path):
                with open(config_path) as f:
                    raw = yaml.safe_load(f) or {}
            else:
                # Fail loud rather than silently running on defaults.
                logging.getLogger("bott-mail").warning(
                    "BOTT_MAIL_CONFIG=%s does not exist; using built-in defaults.",
                    config_path,
                )

        server_raw = raw.get("server", {}) or {}
        server = ServerConfig(
            host=server_raw.get("host", ServerConfig.host),
            port=int(server_raw.get("port", ServerConfig.port)),
        )

        auth_tokens = _load_auth_tokens(raw.get("auth", {}) or {})

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
            mode=str(sync_raw.get("mode", SyncConfig.mode)).lower(),
            idle_folders=list(sync_raw.get("idle_folders", ["INBOX"])),
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
            archive_folder=str(
                safety_raw.get("archive_folder", SafetyConfig.archive_folder)
            ),
        )

        hermes_raw = raw.get("hermes", {}) or {}
        webhook_secret_env = hermes_raw.get(
            "webhook_secret_env", "BOTT_MAIL_HERMES_WEBHOOK_SECRET"
        )
        hermes = HermesConfig(
            webhook_url=str(hermes_raw.get("webhook_url", "")),
            webhook_secret=os.environ.get(webhook_secret_env, ""),
            webhook_timeout_seconds=float(
                hermes_raw.get(
                    "webhook_timeout_seconds", HermesConfig.webhook_timeout_seconds
                )
            ),
        )

        automation_raw = raw.get("automation", {}) or {}
        automation = AutomationConfig(
            auto_classify_new_mail=bool(
                automation_raw.get(
                    "auto_classify_new_mail",
                    AutomationConfig.auto_classify_new_mail,
                )
            ),
            auto_classify_limit_per_sync=int(
                automation_raw.get(
                    "auto_classify_limit_per_sync",
                    AutomationConfig.auto_classify_limit_per_sync,
                )
            ),
        )

        return cls(
            server=server,
            imap=imap,
            sync=sync,
            storage=storage,
            safety=safety,
            hermes=hermes,
            automation=automation,
            auth_tokens=auth_tokens,
            account=raw.get("account", "default"),
        )


def _load_auth_tokens(auth_raw: dict) -> list[AuthTokenConfig]:
    tokens_raw = auth_raw.get("tokens") or []
    tokens: list[AuthTokenConfig] = []

    for item in tokens_raw:
        token_id = str(item.get("id", "")).strip()
        token_env = str(item.get("token_env", item.get("env", ""))).strip()
        scopes = [str(s).strip() for s in item.get("scopes", []) if str(s).strip()]
        token = os.environ.get(token_env, "") if token_env else ""
        if token_id and token and scopes:
            tokens.append(AuthTokenConfig(token_id=token_id, token=token, scopes=scopes))

    if not tokens:
        raise ValueError("No auth tokens configured. Set auth.tokens.")
    return tokens
