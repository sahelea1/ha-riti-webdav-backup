"""Backblaze B2 storage backend using the b2sdk (v2) high-level API.

b2sdk is fully blocking, so every network call is run in the default thread
pool executor via ``run_in_executor`` to keep the asyncio event loop free.
``b2sdk`` is imported lazily inside the client builder so that importing this
module never requires the library unless the backend is actually used.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, List

from .base import RemoteFile, StorageBackend, StorageError

log = logging.getLogger("ritibackup.storage.b2")


class B2Backend(StorageBackend):
    """Backblaze B2 object storage backend.

    Files are stored under ``<prefix>/<name>`` where ``prefix`` is
    ``cfg.b2_prefix`` with surrounding slashes stripped (omitted entirely when
    empty).
    """

    name = "b2"
    label = "Backblaze B2"

    def __init__(self, cfg) -> None:
        self._cfg = cfg
        self._bucket_name = cfg.b2_bucket
        self._prefix = (cfg.b2_prefix or "").strip("/")
        self._api: Any = None
        self._bucket: Any = None

    # --- client / key helpers -------------------------------------------
    def _connect(self) -> Any:
        """Authorize and resolve the bucket (blocking). Lazy + cached."""
        from b2sdk.v2 import B2Api, InMemoryAccountInfo

        cfg = self._cfg
        info = InMemoryAccountInfo()
        api = B2Api(info)
        api.authorize_account("production", cfg.b2_key_id, cfg.b2_application_key)
        bucket = api.get_bucket_by_name(self._bucket_name)
        self._api = api
        self._bucket = bucket
        return bucket

    def _get_bucket(self) -> Any:
        if self._bucket is None:
            self._connect()
        return self._bucket

    def _file_name(self, name: str) -> str:
        return f"{self._prefix}/{name}" if self._prefix else name

    async def _run(self, fn) -> Any:
        """Run a blocking callable in the default executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn)

    def _map_error(self, exc: Exception, action: str) -> StorageError:
        """Map a b2sdk exception to a readable StorageError."""
        try:
            from b2sdk.v2.exception import (
                B2Error,
                NonExistentBucket,
                Unauthorized,
            )
        except Exception:  # noqa: BLE001 - exception module/layout differences
            B2Error = NonExistentBucket = Unauthorized = ()  # type: ignore

        if NonExistentBucket and isinstance(exc, NonExistentBucket):
            return StorageError(
                f"B2 {action} failed: bucket {self._bucket_name!r} does not exist"
            )
        if Unauthorized and isinstance(exc, Unauthorized):
            return StorageError(
                f"B2 {action} failed: unauthorized (check key id / application key)"
            )
        if B2Error and isinstance(exc, B2Error):
            return StorageError(f"B2 {action} failed: {exc}")
        return StorageError(f"B2 {action} failed: {exc}")

    @staticmethod
    def _modified_from_version(fv: Any) -> "datetime | None":
        """Best-effort tz-aware UTC datetime from a B2 FileVersion."""
        ts = getattr(fv, "upload_timestamp", None)
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    # --- operations ------------------------------------------------------
    async def check(self) -> None:
        try:
            await self._run(self._connect)
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, "check") from exc

    async def ensure_ready(self) -> None:
        # Prefixes are implicit in B2; just verify the bucket is reachable.
        await self.check()

    async def upload(self, local_path: str, name: str) -> None:
        bucket = self._get_bucket()
        file_name = self._file_name(name)
        try:
            await self._run(
                lambda: bucket.upload_local_file(
                    local_file=local_path, file_name=file_name
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, "upload") from exc

    async def download(self, name: str, local_path: str) -> int:
        bucket = self._get_bucket()
        file_name = self._file_name(name)

        def _download() -> None:
            downloaded = bucket.download_file_by_name(file_name)
            downloaded.save_to(local_path)

        try:
            await self._run(_download)
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, "download") from exc
        return os.path.getsize(local_path)

    async def delete(self, name: str) -> None:
        bucket = self._get_bucket()
        file_name = self._file_name(name)

        def _delete() -> None:
            file_version = bucket.get_file_info_by_name(file_name)
            self._api.delete_file_version(
                file_version.id_, file_version.file_name
            )

        try:
            await self._run(_delete)
        except Exception as exc:  # noqa: BLE001
            # Treat a missing file as already-deleted.
            try:
                from b2sdk.v2.exception import FileNotPresent

                if isinstance(exc, FileNotPresent):
                    return
            except Exception:  # noqa: BLE001
                pass
            raise self._map_error(exc, "delete") from exc

    async def list(self) -> List[RemoteFile]:
        bucket = self._get_bucket()
        folder = self._prefix

        def _list() -> List[RemoteFile]:
            files: List[RemoteFile] = []
            for file_version, _folder_name in bucket.ls(
                folder_to_list=folder, recursive=False, latest_only=True
            ):
                # ``folder_name`` is set for sub-folders; skip those.
                if _folder_name:
                    continue
                full_name = file_version.file_name
                rel = full_name[len(folder) + 1:] if folder else full_name
                # Defensive: only keep entries directly under the prefix.
                if not rel or "/" in rel:
                    continue
                files.append(
                    RemoteFile(
                        name=rel,
                        size=int(getattr(file_version, "size", 0) or 0),
                        modified=self._modified_from_version(file_version),
                        backend=self.name,
                    )
                )
            return files

        try:
            return await self._run(_list)
        except Exception as exc:  # noqa: BLE001
            try:
                from b2sdk.v2.exception import NonExistentBucket

                if isinstance(exc, NonExistentBucket):
                    return []
            except Exception:  # noqa: BLE001
                pass
            raise self._map_error(exc, "list") from exc
