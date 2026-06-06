"""Orchestration of backup / restore / retention jobs with live progress."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import crypto
from .config import Config, HistoryItem, State, iso, now_utc
from .retention import BackupEntry, plan_retention
from .supervisor import SupervisorClient, SupervisorError
from .webdav import RemoteFile, WebDavClient, WebDavError

log = logging.getLogger("ritibackup.jobs")

SUFFIX = ".tar.riti"
NAME_RE = re.compile(r"^(?P<prefix>.+)_(?P<ts>\d{8}T\d{6}Z)\.tar\.riti$")


def remote_name(prefix: str, when: datetime) -> str:
    return f"{prefix}_{when.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{SUFFIX}"


def parse_remote_time(name: str) -> Optional[datetime]:
    m = NAME_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group("ts"), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


@dataclass
class JobStatus:
    active: bool = False
    kind: str = ""          # backup | restore | retention | test
    step: str = ""          # human-readable current step
    progress: float = 0.0   # 0..1 best-effort
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Optional[str] = None   # success | failed
    message: str = ""
    log_lines: List[str] = field(default_factory=list)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "kind": self.kind,
            "step": self.step,
            "progress": round(self.progress, 3),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "message": self.message,
            "log": self.log_lines[-60:],
        }


class JobManager:
    def __init__(self, cfg: Config, state: State) -> None:
        self.cfg = cfg
        self.state = state
        self.sv = SupervisorClient()
        self.status = JobStatus()
        self._lock = asyncio.Lock()

    # --- helpers ---------------------------------------------------------
    def _dav(self) -> WebDavClient:
        return WebDavClient(
            self.cfg.webdav_url,
            self.cfg.webdav_username,
            self.cfg.webdav_password,
            self.cfg.webdav_verify_ssl,
        )

    def _set(self, *, step: str | None = None, progress: float | None = None) -> None:
        if step is not None:
            self.status.step = step
            self.status.log_lines.append(f"{datetime.now().strftime('%H:%M:%S')}  {step}")
            log.info(step)
        if progress is not None:
            self.status.progress = progress

    def _begin(self, kind: str) -> None:
        self.status = JobStatus(
            active=True, kind=kind, started_at=iso(now_utc()), step="Starting…"
        )

    def _end(self, result: str, message: str = "") -> None:
        self.status.active = False
        self.status.result = result
        self.status.message = message
        self.status.finished_at = iso(now_utc())
        self.status.progress = 1.0 if result == "success" else self.status.progress

    # --- WebDAV connectivity test ---------------------------------------
    async def test_connection(self) -> Dict[str, Any]:
        dav = self._dav()
        await dav.check()
        await dav.ensure_dir(self.cfg.remote_dir)
        files = await self.list_remote()
        return {"ok": True, "backups": len(files)}

    # --- listing ---------------------------------------------------------
    async def list_remote(self) -> List[RemoteFile]:
        dav = self._dav()
        files = await dav.list_dir(self.cfg.remote_dir)
        return [
            f for f in files
            if not f.is_dir and f.name.endswith(SUFFIX)
        ]

    async def remote_entries(self) -> List[BackupEntry]:
        out = []
        for f in await self.list_remote():
            ts = parse_remote_time(f.name) or f.modified or now_utc()
            out.append(BackupEntry(id=f.name, timestamp=ts, size=f.size))
        return out

    # --- BACKUP ----------------------------------------------------------
    async def run_backup(self, kind: str = "manual") -> Dict[str, Any]:
        async with self._lock:
            self._begin("backup")
            t0 = time.monotonic()
            when = now_utc()
            slug = None
            tmp_enc = None
            history = HistoryItem(
                timestamp=iso(when), remote_name="", status="running", kind=kind
            )
            try:
                missing = self.cfg.configured()
                if missing:
                    raise RuntimeError(f"Missing configuration: {', '.join(missing)}")

                name = f"{self.cfg.backup_name_prefix} {when.strftime('%Y-%m-%d %H:%M')}"
                self._set(step="Asking Supervisor to create a full backup…", progress=0.05)
                slug = await self.sv.create_full_backup(
                    name, compressed=self.cfg.compress_supervisor_backup
                )
                tar_path = self.sv.local_tar_path(slug)
                # Some Supervisor versions stage under a subfolder; fall back to info.
                if not os.path.exists(tar_path):
                    await asyncio.sleep(2)
                if not os.path.exists(tar_path):
                    raise RuntimeError(f"Backup tar not found at {tar_path}")
                pt_size = os.path.getsize(tar_path)
                self._set(
                    step=f"Backup created ({_human(pt_size)}). Encrypting with ChaCha20-Poly1305…",
                    progress=0.4,
                )

                rname = remote_name(self.cfg.backup_name_prefix, when)
                history.remote_name = rname
                fd, tmp_enc = tempfile.mkstemp(prefix="riti-", suffix=".enc")
                os.close(fd)

                enc_size = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: crypto.encrypt_file(
                        tar_path,
                        tmp_enc,
                        self.cfg.encryption_passphrase,
                        chunk_size=self.cfg.chunk_size_bytes,
                        n_log2=self.cfg.kdf_n_log2,
                    ),
                )
                self._set(
                    step=f"Encrypted ({_human(enc_size)}). Uploading to WebDAV…",
                    progress=0.6,
                )

                dav = self._dav()
                await dav.ensure_dir(self.cfg.remote_dir)
                await dav.upload(tmp_enc, f"{self.cfg.remote_dir}/{rname}")
                self._set(step="Upload complete. Applying retention policy…", progress=0.85)

                # Clean up local artifacts
                if self.cfg.delete_local_after_upload and slug:
                    try:
                        await self.sv.delete_backup(slug)
                    except SupervisorError as exc:
                        log.warning("Could not delete local backup %s: %s", slug, exc)

                pruned = await self._apply_retention(dav)
                self._set(
                    step=f"Done. Pruned {pruned} old backup(s).", progress=1.0
                )

                dur = time.monotonic() - t0
                history.status = "success"
                history.plaintext_size = pt_size
                history.encrypted_size = enc_size
                history.duration_seconds = round(dur, 1)
                history.message = f"Pruned {pruned} old backup(s)"
                self.state.last_backup_at = iso(when)
                self.state.last_result = "success"
                self.state.last_message = "Backup uploaded successfully"
                self._end("success", "Backup uploaded successfully")
                return {"ok": True, "remote_name": rname, "encrypted_size": enc_size}

            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                log.exception("Backup failed: %s", msg)
                history.status = "failed"
                history.message = msg
                self.state.last_result = "failed"
                self.state.last_message = msg
                self._end("failed", msg)
                return {"ok": False, "error": msg}
            finally:
                if tmp_enc and os.path.exists(tmp_enc):
                    try:
                        os.remove(tmp_enc)
                    except OSError:
                        pass
                self.state.add_history(history)

    async def _apply_retention(self, dav: WebDavClient) -> int:
        entries = await self.remote_entries()
        plan = plan_retention(
            entries,
            now_utc(),
            keep_recent=self.cfg.keep_recent,
            archive_age_days=self.cfg.archive_age_days,
            keep_bridge=self.cfg.keep_bridge,
        )
        for e in plan.delete:
            await dav.delete(f"{self.cfg.remote_dir}/{e.id}")
            log.info("Retention: deleted %s", e.id)
        return len(plan.delete)

    async def run_retention(self) -> Dict[str, Any]:
        async with self._lock:
            self._begin("retention")
            try:
                dav = self._dav()
                pruned = await self._apply_retention(dav)
                self._end("success", f"Pruned {pruned} backup(s)")
                return {"ok": True, "pruned": pruned}
            except Exception as exc:  # noqa: BLE001
                self._end("failed", str(exc))
                return {"ok": False, "error": str(exc)}

    # --- RESTORE ---------------------------------------------------------
    async def run_restore(
        self, remote_filename: str, passphrase: str, *, trigger_restore: bool
    ) -> Dict[str, Any]:
        async with self._lock:
            self._begin("restore")
            tmp_enc = tmp_tar = None
            try:
                fd, tmp_enc = tempfile.mkstemp(prefix="riti-dl-", suffix=".enc")
                os.close(fd)
                fd, tmp_tar = tempfile.mkstemp(
                    dir="/backup" if os.path.isdir("/backup") else None,
                    prefix="riti-restore-",
                    suffix=".tar",
                )
                os.close(fd)

                self._set(step=f"Downloading {remote_filename}…", progress=0.1)
                dav = self._dav()
                await dav.download(
                    f"{self.cfg.remote_dir}/{remote_filename}", tmp_enc
                )

                self._set(step="Decrypting and verifying…", progress=0.45)
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: crypto.decrypt_file(tmp_enc, tmp_tar, passphrase),
                )

                self._set(step="Registering backup with Supervisor…", progress=0.75)
                slug = await self.sv.upload_backup(tmp_tar)

                if trigger_restore:
                    self._set(
                        step="Starting full restore (Home Assistant will reboot)…",
                        progress=0.95,
                    )
                    await self.sv.restore_full(slug)
                    self._end("success", "Restore started; Home Assistant is rebooting")
                else:
                    self._end(
                        "success",
                        f"Decrypted and imported as backup '{slug}'. "
                        "Restore it from Settings → System → Backups.",
                    )
                return {"ok": True, "slug": slug}
            except crypto.DecryptionError as exc:
                self._end("failed", str(exc))
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                log.exception("Restore failed")
                self._end("failed", str(exc))
                return {"ok": False, "error": str(exc)}
            finally:
                for p in (tmp_enc, tmp_tar):
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass


def _human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} TB"
