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
from .storage import RemoteFile, StorageBackend, StorageError, build_backends
from .supervisor import SupervisorClient, SupervisorError

log = logging.getLogger("ritibackup.jobs")

ENC_SUFFIX = ".tar.riti"
PLAIN_SUFFIX = ".tar"
# Accept both encrypted (.tar.riti) and plain (.tar) backups; capture the ts.
NAME_RE = re.compile(r"^(?P<prefix>.+)_(?P<ts>\d{8}T\d{6}Z)\.tar(?:\.riti)?$")


def is_encrypted_name(name: str) -> bool:
    """True if a backup filename is an encrypted RitiBackup container."""
    return name.endswith(ENC_SUFFIX)


def remote_name(prefix: str, when: datetime, encrypted: bool) -> str:
    ts = when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = ENC_SUFFIX if encrypted else PLAIN_SUFFIX
    return f"{prefix}_{ts}{suffix}"


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


def is_backup_name(name: str) -> bool:
    """True if a filename matches the RitiBackup naming pattern."""
    return NAME_RE.match(name) is not None


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
    def _backends(self) -> List[StorageBackend]:
        """Instantiate one client per enabled storage backend."""
        return build_backends(self.cfg)

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

    # --- connectivity test (per enabled backend) ------------------------
    async def test_backends(self) -> List[Dict[str, Any]]:
        """Test each ENABLED backend: check -> ensure_ready -> count backups."""
        results: List[Dict[str, Any]] = []
        for backend in self._backends():
            entry: Dict[str, Any] = {
                "backend": backend.name,
                "label": backend.label,
                "ok": False,
            }
            try:
                await backend.check()
                await backend.ensure_ready()
                files = [f for f in await backend.list() if is_backup_name(f.name)]
                entry["ok"] = True
                entry["backups"] = len(files)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
            results.append(entry)
        return results

    # --- listing ---------------------------------------------------------
    async def list_remote(self) -> tuple[List[RemoteFile], List[Dict[str, str]]]:
        """Aggregate backup files across all enabled backends.

        Returns ``(files, errors)`` where ``errors`` is a list of
        ``{"backend", "error"}`` for backends that could not be listed.
        """
        files: List[RemoteFile] = []
        errors: List[Dict[str, str]] = []
        for backend in self._backends():
            try:
                for f in await backend.list():
                    if is_backup_name(f.name):
                        files.append(f)
            except Exception as exc:  # noqa: BLE001
                errors.append({"backend": backend.name, "error": str(exc)})
        return files, errors

    async def remote_entries(self, backend: StorageBackend) -> List[BackupEntry]:
        """Retention entries for a single backend's files."""
        out: List[BackupEntry] = []
        for f in await backend.list():
            if not is_backup_name(f.name):
                continue
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
            encrypted = bool(self.cfg.encryption_enabled)
            history = HistoryItem(
                timestamp=iso(when), remote_name="", status="running", kind=kind
            )
            try:
                missing = self.cfg.configured()
                if missing:
                    raise RuntimeError(f"Missing configuration: {', '.join(missing)}")

                backends = self._backends()
                if not backends:
                    raise RuntimeError("No storage backend is enabled")

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

                rname = remote_name(self.cfg.backup_name_prefix, when, encrypted)
                history.remote_name = rname

                if encrypted:
                    self._set(
                        step=f"Backup created ({_human(pt_size)}). "
                        "Encrypting with ChaCha20-Poly1305…",
                        progress=0.35,
                    )
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
                    source = tmp_enc
                    self._set(
                        step=f"Encrypted ({_human(enc_size)}). Uploading…",
                        progress=0.5,
                    )
                else:
                    # Upload the plain Supervisor tar directly (no encryption).
                    enc_size = pt_size
                    source = tar_path
                    self._set(
                        step=f"Backup created ({_human(pt_size)}). "
                        "Uploading (unencrypted)…",
                        progress=0.5,
                    )

                # Upload to every enabled backend; track per-backend outcomes.
                uploaded: List[str] = []
                upload_errors: List[Dict[str, str]] = []
                total = len(backends)
                pruned_total = 0
                for i, backend in enumerate(backends):
                    base = 0.5 + 0.4 * (i / total)
                    self._set(
                        step=f"Uploading to {backend.label}…",
                        progress=round(base, 3),
                    )
                    try:
                        await backend.ensure_ready()
                        await backend.upload(source, rname)
                        uploaded.append(backend.label)
                        pruned_total += await self._apply_retention(backend)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("Upload to %s failed", backend.name)
                        upload_errors.append(
                            {"backend": backend.name, "error": str(exc)}
                        )

                if not uploaded:
                    details = "; ".join(
                        f"{e['backend']}: {e['error']}" for e in upload_errors
                    )
                    raise RuntimeError(f"All backend uploads failed ({details})")

                # Clean up local Supervisor artifact only after at least one
                # successful upload.
                if self.cfg.delete_local_after_upload and slug:
                    try:
                        await self.sv.delete_backup(slug)
                    except SupervisorError as exc:
                        log.warning("Could not delete local backup %s: %s", slug, exc)

                if upload_errors:
                    failed = ", ".join(e["backend"] for e in upload_errors)
                    msg = (
                        f"Uploaded to {', '.join(uploaded)}; "
                        f"failed: {failed}. Pruned {pruned_total} old backup(s)."
                    )
                else:
                    msg = (
                        f"Uploaded to {', '.join(uploaded)}. "
                        f"Pruned {pruned_total} old backup(s)."
                    )
                self._set(step=f"Done. {msg}", progress=1.0)

                dur = time.monotonic() - t0
                history.status = "success"
                history.plaintext_size = pt_size
                history.encrypted_size = enc_size
                history.duration_seconds = round(dur, 1)
                history.message = msg
                self.state.last_backup_at = iso(when)
                self.state.last_result = "success"
                self.state.last_message = msg
                self._end("success", msg)
                return {
                    "ok": True,
                    "remote_name": rname,
                    "uploaded": uploaded,
                    "errors": upload_errors,
                }

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

    # --- DELETE / CLEAR / UPLOAD ----------------------------------------
    def _enabled_backend(self, backend_name: str) -> Optional[StorageBackend]:
        """Return the enabled backend whose ``.name`` matches, else None."""
        for backend in build_backends(self.cfg):
            if backend.name == backend_name:
                return backend
        return None

    async def delete_one(self, name: str, backend_name: str) -> Dict[str, Any]:
        """Delete a single backup file from one enabled backend."""
        async with self._lock:
            try:
                backend = self._enabled_backend(backend_name)
                if backend is None:
                    return {
                        "ok": False,
                        "error": f"Backend '{backend_name}' not enabled",
                    }
                await backend.delete(name)
                return {
                    "ok": True,
                    "deleted": True,
                    "name": name,
                    "backend": backend_name,
                }
            except (StorageError, Exception) as exc:  # noqa: BLE001
                log.exception("Delete of %s on %s failed", name, backend_name)
                return {"ok": False, "error": str(exc)}

    async def clear_all(self, backend_name: Optional[str]) -> Dict[str, Any]:
        """Delete all RitiBackup files on one or all enabled backends."""
        async with self._lock:
            self._begin("clear")
            deleted = 0
            errors: List[Dict[str, str]] = []
            try:
                all_backends = build_backends(self.cfg)
                if backend_name in (None, "", "all"):
                    targets = all_backends
                else:
                    match = next(
                        (b for b in all_backends if b.name == backend_name), None
                    )
                    if match is None:
                        self._end("failed", f"Backend '{backend_name}' not enabled")
                        return {
                            "ok": False,
                            "error": f"Backend '{backend_name}' not enabled",
                        }
                    targets = [match]

                for backend in targets:
                    self._set(step=f"Clearing {backend.label}…")
                    try:
                        for f in await backend.list():
                            if (
                                parse_remote_time(f.name) is not None
                                or is_encrypted_name(f.name)
                            ):
                                await backend.delete(f.name)
                                deleted += 1
                    except Exception as exc:  # noqa: BLE001
                        log.exception("Clear on %s failed", backend.name)
                        errors.append({"backend": backend.name, "error": str(exc)})

                msg = f"Deleted {deleted} backup(s)"
                if errors:
                    msg += f"; {len(errors)} backend(s) errored"
                self._end("success", msg)
                return {"ok": True, "deleted": deleted, "errors": errors}
            except Exception as exc:  # noqa: BLE001
                log.exception("Clear failed")
                self._end("failed", str(exc))
                return {"ok": False, "error": str(exc)}

    async def run_upload(
        self,
        temp_path: str,
        original_filename: str,
        target_backend_names: Optional[List[str]],
        encrypt: bool,
    ) -> Dict[str, Any]:
        """Upload a user-supplied backup file to the selected backend(s)."""
        async with self._lock:
            self._begin("upload")
            tmp_enc = None
            try:
                # 1. Is the file already an encrypted RitiBackup container?
                already_enc = original_filename.endswith(ENC_SUFFIX)
                if not already_enc:
                    try:
                        with open(temp_path, "rb") as fh:
                            head = fh.read(len(crypto.MAGIC))
                        if head == crypto.MAGIC:
                            already_enc = True
                    except OSError:
                        pass

                source = temp_path
                final_encrypted = already_enc

                # 2. Optionally encrypt a plain tar before upload.
                if encrypt and not already_enc:
                    if not self.cfg.encryption_passphrase:
                        raise RuntimeError(
                            "Encryption requested but no encryption passphrase is "
                            "configured. Set a passphrase in the add-on options or "
                            "upload without encryption."
                        )
                    self._set(step="Encrypting…", progress=0.2)
                    fd, tmp_enc = tempfile.mkstemp(
                        dir=os.path.dirname(temp_path) or None,
                        prefix="riti-upload-enc-",
                        suffix=ENC_SUFFIX,
                    )
                    os.close(fd)
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: crypto.encrypt_file(
                            temp_path,
                            tmp_enc,
                            self.cfg.encryption_passphrase,
                            chunk_size=self.cfg.chunk_size_bytes,
                            n_log2=self.cfg.kdf_n_log2,
                        ),
                    )
                    source = tmp_enc
                    final_encrypted = True

                # 3. Decide the final remote name.
                if (
                    NAME_RE.match(original_filename)
                    and is_encrypted_name(original_filename) == final_encrypted
                ):
                    final_name = original_filename
                else:
                    final_name = remote_name(
                        self.cfg.backup_name_prefix, now_utc(), final_encrypted
                    )

                # 4. Determine target backends.
                backends = build_backends(self.cfg)
                if target_backend_names:
                    wanted = set(target_backend_names)
                    backends = [b for b in backends if b.name in wanted]
                if not backends:
                    raise RuntimeError("No matching storage backend is enabled")

                # 5. Upload to each target backend.
                uploaded: List[str] = []
                errors: List[Dict[str, str]] = []
                total = len(backends)
                for i, backend in enumerate(backends):
                    self._set(
                        step=f"Uploading to {backend.label}…",
                        progress=round(0.3 + 0.6 * (i / total), 3),
                    )
                    try:
                        await backend.ensure_ready()
                        await backend.upload(source, final_name)
                        uploaded.append(backend.name)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("Upload to %s failed", backend.name)
                        errors.append({"backend": backend.name, "error": str(exc)})

                ok = len(uploaded) > 0
                if ok:
                    labels = ", ".join(uploaded)
                    msg = f"Uploaded {final_name} to {labels}"
                    if errors:
                        msg += f"; failed: {', '.join(e['backend'] for e in errors)}"
                    self._set(step=f"Done. {msg}", progress=1.0)
                    self._end("success", msg)
                else:
                    details = "; ".join(
                        f"{e['backend']}: {e['error']}" for e in errors
                    )
                    msg = f"All uploads failed ({details})" if details else "Upload failed"
                    self._end("failed", msg)
                return {
                    "ok": ok,
                    "name": final_name,
                    "backends": uploaded,
                    "errors": errors,
                }
            except Exception as exc:  # noqa: BLE001
                log.exception("Upload job failed")
                self._end("failed", str(exc))
                return {"ok": False, "error": str(exc)}
            finally:
                for p in (temp_path, tmp_enc):
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass

    async def _apply_retention(self, backend: StorageBackend) -> int:
        """Apply the retention policy to a single backend and delete on it."""
        entries = await self.remote_entries(backend)
        plan = plan_retention(
            entries,
            now_utc(),
            keep_recent=self.cfg.keep_recent,
            archive_age_days=self.cfg.archive_age_days,
            keep_bridge=self.cfg.keep_bridge,
        )
        for e in plan.delete:
            await backend.delete(e.id)
            log.info("Retention [%s]: deleted %s", backend.name, e.id)
        return len(plan.delete)

    async def run_retention(self) -> Dict[str, Any]:
        async with self._lock:
            self._begin("retention")
            try:
                pruned = 0
                errors: List[Dict[str, str]] = []
                for backend in self._backends():
                    try:
                        pruned += await self._apply_retention(backend)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("Retention on %s failed", backend.name)
                        errors.append({"backend": backend.name, "error": str(exc)})
                if errors and pruned == 0 and len(errors) == len(self._backends()):
                    details = "; ".join(
                        f"{e['backend']}: {e['error']}" for e in errors
                    )
                    self._end("failed", details)
                    return {"ok": False, "error": details}
                self._end("success", f"Pruned {pruned} backup(s)")
                return {"ok": True, "pruned": pruned}
            except Exception as exc:  # noqa: BLE001
                self._end("failed", str(exc))
                return {"ok": False, "error": str(exc)}

    # --- RESTORE ---------------------------------------------------------
    async def run_restore(
        self,
        remote_filename: str,
        backend_name: str,
        passphrase: str,
        *,
        trigger_restore: bool,
    ) -> Dict[str, Any]:
        async with self._lock:
            self._begin("restore")
            tmp_dl = tmp_tar = None
            encrypted = is_encrypted_name(remote_filename)
            try:
                backend = next(
                    (b for b in self._backends() if b.name == backend_name), None
                )
                if backend is None:
                    raise RuntimeError(
                        f"Backend {backend_name!r} is not enabled or unknown"
                    )
                if encrypted and not passphrase:
                    raise RuntimeError(
                        "This backup is encrypted; a passphrase is required to "
                        "restore it."
                    )

                fd, tmp_dl = tempfile.mkstemp(prefix="riti-dl-", suffix=".bin")
                os.close(fd)

                self._set(
                    step=f"Downloading {remote_filename} from {backend.label}…",
                    progress=0.1,
                )
                await backend.download(remote_filename, tmp_dl)

                if encrypted:
                    fd, tmp_tar = tempfile.mkstemp(
                        dir="/backup" if os.path.isdir("/backup") else None,
                        prefix="riti-restore-",
                        suffix=".tar",
                    )
                    os.close(fd)
                    self._set(step="Decrypting and verifying…", progress=0.45)
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: crypto.decrypt_file(tmp_dl, tmp_tar, passphrase),
                    )
                    tar_path = tmp_tar
                else:
                    # Already a plain Supervisor tar; upload it directly.
                    tar_path = tmp_dl

                self._set(step="Registering backup with Supervisor…", progress=0.75)
                slug = await self.sv.upload_backup(tar_path)

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
                        f"Imported as backup '{slug}'. "
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
                for p in (tmp_dl, tmp_tar):
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
