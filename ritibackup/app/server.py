"""aiohttp web server: JSON API + static single-page UI (served under Ingress)."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from aiohttp import web

from .config import Config, State, iso, now_utc
from .jobs import JobManager, parse_remote_time
from .scheduler import Scheduler

log = logging.getLogger("ritibackup.server")

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


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
        return web.json_response(
            {
                "now": iso(now_utc()),
                "supervisor_ok": sv_ok,
                "ha_version": ha_version,
                "configured": not self.cfg.configured(),
                "missing": self.cfg.configured(),
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
            files = await self.jobs.list_remote()
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=200)
        items = []
        for f in files:
            ts = parse_remote_time(f.name) or f.modified
            items.append(
                {
                    "name": f.name,
                    "size": f.size,
                    "size_h": _human(f.size),
                    "timestamp": iso(ts) if ts else None,
                }
            )
        items.sort(key=lambda x: x["timestamp"] or "", reverse=True)
        return web.json_response({"ok": True, "backups": items})

    async def api_test(self, request: web.Request) -> web.Response:
        try:
            res = await self.jobs.test_connection()
            return web.json_response(res)
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)})

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
        passphrase = body.get("passphrase") or self.cfg.encryption_passphrase
        trigger = bool(body.get("trigger_restore", False))
        if not name:
            return web.json_response({"ok": False, "error": "No backup selected"})
        self._spawn(
            self.jobs.run_restore(name, passphrase, trigger_restore=trigger)
        )
        return web.json_response({"ok": True, "started": True})


def build_app(cfg: Config, state: State, jobs: JobManager, sched: Scheduler) -> web.Application:
    return Server(cfg, state, jobs, sched).app
