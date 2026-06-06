"""Configuration (from Supervisor options) and persistent state."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

log = logging.getLogger("ritibackup.config")

OPTIONS_PATH = os.environ.get("RITI_OPTIONS_PATH", "/data/options.json")
STATE_PATH = os.environ.get("RITI_STATE_PATH", "/data/state.json")


@dataclass
class Config:
    # --- Encryption (optional, off by default) ---------------------------
    encryption_enabled: bool = False
    encryption_passphrase: str = ""

    # --- WebDAV backend --------------------------------------------------
    webdav_enabled: bool = False
    webdav_url: str = ""
    webdav_username: str = ""
    webdav_password: str = ""
    webdav_path: str = "/RitiBackup"
    webdav_verify_ssl: bool = True

    # --- S3 (and S3-compatible) backend ----------------------------------
    s3_enabled: bool = False
    s3_endpoint_url: str = ""             # blank = AWS S3
    s3_region: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_prefix: str = "RitiBackup"
    s3_path_style: bool = False

    # --- Backblaze B2 backend --------------------------------------------
    b2_enabled: bool = False
    b2_key_id: str = ""
    b2_application_key: str = ""
    b2_bucket: str = ""
    b2_prefix: str = "RitiBackup"

    schedule_time: str = "03:00"          # HH:MM, 24h, addon local time
    schedule_interval_days: int = 2       # every second day
    run_missed_on_start: bool = True

    keep_recent: int = 4
    archive_age_days: int = 14
    keep_bridge: bool = False

    kdf_n_log2: int = 16                  # scrypt N = 2**16
    chunk_size_kib: int = 64

    backup_name_prefix: str = "RitiBackup"
    delete_local_after_upload: bool = True
    compress_supervisor_backup: bool = True

    log_level: str = "info"

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if os.path.exists(OPTIONS_PATH):
            try:
                with open(OPTIONS_PATH, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                for k, v in raw.items():
                    if hasattr(cfg, k) and v is not None:
                        setattr(cfg, k, v)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not read %s: %s", OPTIONS_PATH, exc)
        return cfg

    # --- derived helpers -------------------------------------------------
    @property
    def chunk_size_bytes(self) -> int:
        return max(1, int(self.chunk_size_kib)) * 1024

    @property
    def remote_dir(self) -> str:
        p = "/" + self.webdav_path.strip("/")
        return p if p != "/" else ""

    def redacted(self) -> Dict[str, Any]:
        """Config safe to send to the browser (secrets masked)."""
        d = asdict(self)
        d["webdav_password"] = "********" if self.webdav_password else ""
        d["s3_secret_access_key"] = "********" if self.s3_secret_access_key else ""
        d["b2_application_key"] = "********" if self.b2_application_key else ""
        d["encryption_passphrase"] = "set" if self.encryption_passphrase else ""
        return d

    def configured(self) -> List[str]:
        """Return a list of missing required settings (empty == ready)."""
        missing: List[str] = []
        if not (self.webdav_enabled or self.s3_enabled or self.b2_enabled):
            missing.append("a storage backend (enable WebDAV, S3, or B2)")
        if self.webdav_enabled and not self.webdav_url:
            missing.append("webdav_url")
        if self.s3_enabled:
            if not self.s3_bucket:
                missing.append("s3_bucket")
            if not self.s3_access_key_id:
                missing.append("s3_access_key_id")
            if not self.s3_secret_access_key:
                missing.append("s3_secret_access_key")
        if self.b2_enabled:
            if not self.b2_key_id:
                missing.append("b2_key_id")
            if not self.b2_application_key:
                missing.append("b2_application_key")
            if not self.b2_bucket:
                missing.append("b2_bucket")
        if self.encryption_enabled and not self.encryption_passphrase:
            missing.append("encryption_passphrase")
        return missing

    def backends_summary(self) -> List[Dict[str, Any]]:
        """Describe all three backends (for the UI), in webdav/s3/b2 order."""
        return [
            {"name": "webdav", "label": "WebDAV", "enabled": bool(self.webdav_enabled)},
            {"name": "s3", "label": "S3", "enabled": bool(self.s3_enabled)},
            {"name": "b2", "label": "Backblaze B2", "enabled": bool(self.b2_enabled)},
        ]


@dataclass
class HistoryItem:
    timestamp: str                # ISO UTC, the backup's logical time
    remote_name: str
    status: str                   # success | failed | running
    plaintext_size: int = 0
    encrypted_size: int = 0
    duration_seconds: float = 0.0
    message: str = ""
    kind: str = "scheduled"       # scheduled | manual


@dataclass
class State:
    last_backup_at: str | None = None        # ISO UTC of last *successful* backup
    last_result: str | None = None           # success | failed
    last_message: str = ""
    next_run_at: str | None = None
    history: List[Dict[str, Any]] = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @classmethod
    def load(cls) -> "State":
        st = cls()
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                for k in ("last_backup_at", "last_result", "last_message", "next_run_at"):
                    if k in raw:
                        setattr(st, k, raw[k])
                st.history = raw.get("history", [])
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not read state: %s", exc)
        return st

    def save(self) -> None:
        with self._lock:
            data = {
                "last_backup_at": self.last_backup_at,
                "last_result": self.last_result,
                "last_message": self.last_message,
                "next_run_at": self.next_run_at,
                "history": self.history[-100:],  # cap history
            }
            os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
            tmp = STATE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, STATE_PATH)

    def add_history(self, item: HistoryItem) -> None:
        self.history.append(asdict(item))
        self.save()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
