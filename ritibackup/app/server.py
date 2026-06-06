"""aiohttp web server: JSON API + static single-page UI (served under Ingress)."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone

from aiohttp import web

from .config import Config, State, iso, now_utc
from .jobs import JobManager, is_encrypted_name, parse_remote_time
from .scheduler import Scheduler

log = logging.getLogger("ritibackup.server")

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

BACKEND_LABELS = {"webdav": "WebDAV", "s3": "S3", "b2": "Backblaze B2"}


def _human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} TB"


class Server:
    def __init__(self, cfg: Config, state: State, jobs: JobManager, sched: Scheduler):
        self.cfg = cfg
        self.state = state
        self.jobs = jobs
        self.sched = sched
        self.app = web.Application(client_max_size=0)  # 0 = unlimited
        self._bg_tasks: set[asyncio.Task] = set()
        self._routes()

    def _spawn(self, coro) -> None:
        """Run a coroutine fire-and-forget, holding a reference so the task is
        not garbage-collected before it finishes."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _routes(self) -> None:
        a = self.app
        a.router.add_get("/api/status", self.api_status)
        a.router.add_get("/api/config", self.api_config)
        a.router.add_get("/api/job", self.api_job)
        a.router.add_get("/api/backups", self.api_backups)
        a.router.add_post("/api/test", self.api_test)
        a.router.add_post("/api/backup", self.api_backup)
        a.router.add_post("/api/retention", self.api_retention)
        a.router.add_post("/api/restore", self.api_restore)
        a.router.add_post("/api/delete", self.api_delete)
        a.router.add_post("/api/clear", self.api_clear)
        a.router.add_post("/api/upload", self.api_upload)
        a.router.add_get("/", self.index)
        a.router.add_static("/static/", WEB_DIR, show_index=False)

    # --- pages -----------------------------------------------------------
    async def index(self, request: web.Request) -> web.Response:
        with open(os.path.join(WEB_DIR, "index.html"), "r", encoding="utf-8") as fh:
            html = fh.read()
        return web.Response(text=html, content_type="text/html")

    # --- api -------------------------------------------------------------
    async def api_status(self, request: web.Request) -> web.Response:
        sv_ok = True
        ha_version = ""
        try:
            info = await self.jobs.sv.info()
            ha_version = info.get("homeassistant", "")
        except Exception:  # noqa: BLE001
            sv_ok = False
        missing = self.cfg.configured()
        return web.json_response(
            {
                "now": iso(now_utc()),
                "supervisor_ok": sv_ok,
                "ha_version": ha_version,
                "configured": not missing,
                "missing": missing,
                "encryption_enabled": bool(self.cfg.encryption_enabled),
                "backends": self.cfg.backends_summary(),
                "last_backup_at": self.state.last_backup_at,
                "last_result": self.state.last_result,
                "last_message": self.state.last_message,
                "next_run_at": self.state.next_run_at,
                "history": list(reversed(self.state.history[-25:])),
                "policy": {
                    "keep_recent": self.cfg.keep_recent,
                    "archive_age_days": self.cfg.archive_age_days,
                    "keep_bridge": self.cfg.keep_bridge,
                    "interval_days": self.cfg.schedule_interval_days,
                    "schedule_time": self.cfg.schedule_time,
                },
            }
        )

    async def api_config(self, request: web.Request) -> web.Response:
        return web.json_response(self.cfg.redacted())

    async def api_job(self, request: web.Request) -> web.Response:
        return web.json_response(self.jobs.status.snapshot())

    async def api_backups(self, request: web.Request) -> web.Response:
        try:
            files, errors = await self.jobs.list_remote()
        except Exception as exc:  # noqa: BLE001
            return web.json_response(
                {"ok": False, "backups": [], "errors": [], "error": str(exc)}
            )
        items = []
        for f in files:
            ts = parse_remote_time(f.name) or f.modified
            items.append(
                {
                    "name": f.name,
                    "size": f.size,
                    "size_h": _human(f.size),
                    "timestamp": iso(ts) if ts else None,
                    "backend": f.backend,
                    "backend_label": BACKEND_LABELS.get(f.backend, f.backend),
                    "encrypted": is_encrypted_name(f.name),
                }
            )
        items.sort(key=lambda x: x["timestamp"] or "", reverse=True)
        return web.json_response({"ok": True, "backups": items, "errors": errors})

    async def api_test(self, request: web.Request) -> web.Response:
        try:
            results = await self.jobs.test_backends()
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "results": [], "error": str(exc)})
        if not results:
            return web.json_response(
                {"ok": False, "results": [], "error": "No backends enabled"}
            )
        return web.json_response({"ok": True, "results": results})

    async def api_backup(self, request: web.Request) -> web.Response:
        if self.jobs.status.active:
            return web.json_response({"ok": False, "error": "A job is already running"})
        # Fire and forget; UI polls /api/job
        self._spawn(self._run_backup_and_wake())
        return web.json_response({"ok": True, "started": True})

    async def _run_backup_and_wake(self):
        await self.jobs.run_backup(kind="manual")
        self.sched.wake()

    async def api_retention(self, request: web.Request) -> web.Response:
        if self.jobs.status.active:
            return web.json_response({"ok": False, "error": "A job is already running"})
        res = await self.jobs.run_retention()
        return web.json_response(res)

    async def api_restore(self, request: web.Request) -> web.Response:
        if self.jobs.status.active:
            return web.json_response({"ok": False, "error": "A job is already running"})
        body = await request.json()
        name = body.get("name")
        backend = body.get("backend")
        passphrase = body.get("passphrase") or self.cfg.encryption_passphrase
        trigger = bool(body.get("trigger_restore", False))
        if not name:
            return web.json_response({"ok": False, "error": "No backup selected"})
        if not backend:
            return web.json_response({"ok": False, "error": "No backend selected"})
        self._spawn(
            self.jobs.run_restore(
                name, backend, passphrase, trigger_restore=trigger
            )
        )
        return web.json_response({"ok": True, "started": True})

    async def api_delete(self, request: web.Request) -> web.Response:
        if self.jobs.status.active:
            return web.json_response({"ok": False, "error": "A job is already running"})
        body = await request.json()
        name = body.get("name")
        backend = body.get("backend")
        if not name:
            return web.json_response({"ok": False, "error": "No backup selected"})
        if not backend:
            return web.json_response({"ok": False, "error": "No backend selected"})
        res = await self.jobs.delete_one(name, backend)
        return web.json_response(res)

    async def api_clear(self, request: web.Request) -> web.Response:
        if self.jobs.status.active:
            return web.json_response({"ok": False, "error": "A job is already running"})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        res = await self.jobs.clear_all(body.get("backend"))
        return web.json_response(res)

    async def api_upload(self, request: web.Request) -> web.Response:
        if self.jobs.status.active:
            return web.json_response({"ok": False, "error": "A job is already running"})

        temp_path = None
        original_filename = ""
        backends_raw = ""
        encrypt_raw = None
        name_field = ""
        try:
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "file":
                    original_filename = part.filename or "upload.tar"
                    fd, temp_path = tempfile.mkstemp(
                        dir="/backup" if os.path.isdir("/backup") else None,
                        prefix="riti-upload-",
                        suffix=".bin",
                    )
                    with os.fdopen(fd, "wb") as out:
                        while True:
                            chunk = await part.read_chunk()
                            if not chunk:
                                break
                            out.write(chunk)
                elif part.name == "backends":
                    backends_raw = (await part.text()).strip()
                elif part.name == "encrypt":
                    encrypt_raw = (await part.text()).strip()
                elif part.name == "name":
                    name_field = (await part.text()).strip()

            if temp_path is None or not os.path.exists(temp_path):
                return web.json_response({"ok": False, "error": "No file uploaded"})

            # Parse target backends (comma-separated names; blank/"all" => None).
            backend_list = None
            if backends_raw and backends_raw.lower() != "all":
                backend_list = [
                    b.strip() for b in backends_raw.split(",") if b.strip()
                ] or None

            # Parse encryption flag (default to configured encryption setting).
            if encrypt_raw is None:
                encrypt_bool = bool(self.cfg.encryption_enabled)
            else:
                encrypt_bool = encrypt_raw.lower() in ("true", "1", "on", "yes")

            # Optional desired filename override.
            if name_field:
                original_filename = name_field

            self._spawn(
                self.jobs.run_upload(
                    temp_path, original_filename, backend_list, encrypt_bool
                )
            )
            return web.json_response({"ok": True, "started": True})
        except Exception as exc:  # noqa: BLE001
            log.exception("Upload streaming failed")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return web.json_response({"ok": False, "error": str(exc)})


def build_app(cfg: Config, state: State, jobs: JobManager, sched: Scheduler) -> web.Application:
    return Server(cfg, state, jobs, sched).app
