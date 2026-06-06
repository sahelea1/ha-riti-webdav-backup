"""Scheduler: triggers backups every N days at a fixed local time, with catch-up."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta

from .config import Config, State, iso
from .jobs import JobManager

log = logging.getLogger("ritibackup.scheduler")


def _parse_hhmm(value: str) -> time:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except Exception:  # noqa: BLE001
        return time(3, 0)


class Scheduler:
    def __init__(self, cfg: Config, state: State, jobs: JobManager) -> None:
        self.cfg = cfg
        self.state = state
        self.jobs = jobs
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    def next_run(self, now: datetime) -> datetime:
        """Compute the next scheduled local datetime."""
        run_at = _parse_hhmm(self.cfg.schedule_time)
        interval = max(1, int(self.cfg.schedule_interval_days))

        last = self.state.last_backup_at
        if last:
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone()
            except ValueError:
                last_dt = now
            base_date = last_dt.date() + timedelta(days=interval)
            candidate = datetime.combine(base_date, run_at).astimezone(now.tzinfo)
            # Already run before: keep the configured cadence.
            while candidate <= now:
                candidate += timedelta(days=interval)
        else:
            # Never run: take the next daily occurrence of the configured time
            # so the very first backup happens soon, then the interval kicks in.
            candidate = datetime.combine(now.date(), run_at).astimezone(now.tzinfo)
            if candidate <= now:
                candidate += timedelta(days=1)
        return candidate

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        # Optional catch-up: if a backup was due while the addon was off.
        if self.cfg.run_missed_on_start and self._is_overdue(datetime.now().astimezone()):
            log.info("A scheduled backup is overdue; running catch-up backup now.")
            await self.jobs.run_backup(kind="scheduled")

        while True:
            now = datetime.now().astimezone()
            nxt = self.next_run(now)
            self.state.next_run_at = iso(nxt.astimezone())
            self.state.save()
            delay = max(5.0, (nxt - now).total_seconds())
            log.info("Next scheduled backup at %s (in %.0f min)", nxt, delay / 60)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
                self._wake.clear()
                continue  # config changed / manual wake -> recompute
            except asyncio.TimeoutError:
                pass

            if not self.jobs.status.active:
                log.info("Scheduled backup triggered.")
                await self.jobs.run_backup(kind="scheduled")

    def _is_overdue(self, now: datetime) -> bool:
        if not self.state.last_backup_at:
            return False  # never run: wait for first scheduled slot, don't surprise-backup
        try:
            last_dt = datetime.fromisoformat(
                self.state.last_backup_at.replace("Z", "+00:00")
            ).astimezone()
        except ValueError:
            return False
        interval = max(1, int(self.cfg.schedule_interval_days))
        return now - last_dt >= timedelta(days=interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
