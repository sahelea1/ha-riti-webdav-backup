"""Home Assistant Supervisor API client (backup create / list / upload / restore)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp

log = logging.getLogger("ritibackup.supervisor")

SUPERVISOR_URL = os.environ.get("SUPERVISOR_API", "http://supervisor")
BACKUP_DIR = os.environ.get("RITI_BACKUP_DIR", "/backup")


class SupervisorError(Exception):
    pass


class SupervisorClient:
    def __init__(self) -> None:
        self.token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not self.token:
            log.warning("SUPERVISOR_TOKEN not set; Supervisor API calls will fail")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _session(self, total: Optional[int] = 1800) -> aiohttp.ClientSession:
        timeout = aiohttp.ClientTimeout(total=total, connect=30)
        return aiohttp.ClientSession(headers=self._headers(), timeout=timeout)

    async def _result(self, r: aiohttp.ClientResponse) -> Dict[str, Any]:
        try:
            data = await r.json()
        except Exception:  # noqa: BLE001
            text = await r.text()
            raise SupervisorError(f"HTTP {r.status}: {text[:200]}")
        if data.get("result") != "ok":
            raise SupervisorError(data.get("message", f"HTTP {r.status}"))
        return data.get("data", {})

    async def info(self) -> Dict[str, Any]:
        async with self._session(total=30) as s:
            async with s.get(f"{SUPERVISOR_URL}/info") as r:
                return await self._result(r)

    async def create_full_backup(
        self, name: str, *, compressed: bool = True
    ) -> str:
        """Create an *unencrypted* full backup; we encrypt it ourselves.

        Returns the backup slug. The resulting tar is at /backup/<slug>.tar.
        """
        payload: Dict[str, Any] = {"name": name, "compressed": compressed}
        async with self._session(total=3600) as s:
            async with s.post(
                f"{SUPERVISOR_URL}/backups/new/full", json=payload
            ) as r:
                data = await self._result(r)
        slug = data.get("slug")
        if not slug:
            raise SupervisorError("Supervisor did not return a backup slug")
        return slug

    async def list_backups(self) -> List[Dict[str, Any]]:
        async with self._session(total=60) as s:
            async with s.get(f"{SUPERVISOR_URL}/backups") as r:
                data = await self._result(r)
        return data.get("backups", [])

    async def backup_info(self, slug: str) -> Dict[str, Any]:
        async with self._session(total=60) as s:
            async with s.get(f"{SUPERVISOR_URL}/backups/{slug}/info") as r:
                return await self._result(r)

    async def delete_backup(self, slug: str) -> None:
        async with self._session(total=60) as s:
            async with s.delete(f"{SUPERVISOR_URL}/backups/{slug}") as r:
                await self._result(r)

    def local_tar_path(self, slug: str) -> str:
        return os.path.join(BACKUP_DIR, f"{slug}.tar")

    async def upload_backup(self, tar_path: str) -> str:
        """Register a (decrypted) tar with Supervisor so it can be restored."""
        async with self._session(total=3600) as s:
            with open(tar_path, "rb") as fh:
                form = aiohttp.FormData()
                form.add_field(
                    "file",
                    fh,
                    filename=os.path.basename(tar_path),
                    content_type="application/x-tar",
                )
                async with s.post(
                    f"{SUPERVISOR_URL}/backups/new/upload", data=form
                ) as r:
                    data = await self._result(r)
        slug = data.get("slug")
        if not slug:
            raise SupervisorError("Upload did not return a slug")
        return slug

    async def restore_full(self, slug: str) -> None:
        """Trigger a full restore. Home Assistant will reboot during this."""
        async with self._session(total=3600) as s:
            async with s.post(
                f"{SUPERVISOR_URL}/backups/{slug}/restore/full", json={}
            ) as r:
                await self._result(r)
