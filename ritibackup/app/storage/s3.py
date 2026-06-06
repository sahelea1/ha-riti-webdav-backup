"""S3 (and S3-compatible) storage backend using boto3.

boto3 is fully blocking, so every network call is run in the default thread
pool executor via ``run_in_executor`` to keep the asyncio event loop free.
``boto3``/``botocore`` are imported lazily inside the client builder so that
importing this module never requires the libraries unless the backend is used.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timezone
from typing import Any, List

from .base import RemoteFile, StorageBackend, StorageError

log = logging.getLogger("ritibackup.storage.s3")


class S3Backend(StorageBackend):
    """S3 / S3-compatible object storage backend.

    Files are stored under ``<prefix>/<name>`` where ``prefix`` is
    ``cfg.s3_prefix`` with surrounding slashes stripped (omitted entirely when
    empty).
    """

    name = "s3"
    label = "S3"

    def __init__(self, cfg) -> None:
        self._cfg = cfg
        self._bucket = cfg.s3_bucket
        self._prefix = (cfg.s3_prefix or "").strip("/")
        self._client: Any = None

    # --- client / key helpers -------------------------------------------
    def _build_client(self) -> Any:
        """Construct the (blocking) boto3 S3 client. Lazy + cached."""
        import boto3
        import botocore.config

        cfg = self._cfg
        addressing = "path" if cfg.s3_path_style else "auto"
        client_config = botocore.config.Config(
            signature_version="s3v4",
            s3={"addressing_style": addressing},
        )
        session = boto3.session.Session()
        return session.client(
            "s3",
            endpoint_url=cfg.s3_endpoint_url or None,
            region_name=cfg.s3_region or None,
            aws_access_key_id=cfg.s3_access_key_id or None,
            aws_secret_access_key=cfg.s3_secret_access_key or None,
            config=client_config,
        )

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _key(self, name: str) -> str:
        return f"{self._prefix}/{name}" if self._prefix else name

    async def _run(self, fn) -> Any:
        """Run a blocking callable in the default executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn)

    def _map_error(self, exc: Exception, action: str) -> StorageError:
        """Map a boto3/botocore exception to a readable StorageError."""
        from botocore.exceptions import (
            ClientError,
            EndpointConnectionError,
            NoCredentialsError,
        )

        if isinstance(exc, NoCredentialsError):
            return StorageError(f"S3 {action} failed: missing credentials")
        if isinstance(exc, EndpointConnectionError):
            return StorageError(
                f"S3 {action} failed: cannot reach endpoint ({exc})"
            )
        if isinstance(exc, ClientError):
            err = exc.response.get("Error", {}) if hasattr(exc, "response") else {}
            code = err.get("Code", "?")
            msg = err.get("Message", str(exc))
            return StorageError(f"S3 {action} failed [{code}]: {msg}")
        return StorageError(f"S3 {action} failed: {exc}")

    # --- operations ------------------------------------------------------
    async def check(self) -> None:
        client = self._get_client()
        try:
            await self._run(lambda: client.head_bucket(Bucket=self._bucket))
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, "check") from exc

    async def ensure_ready(self) -> None:
        # Prefixes are implicit in S3; just verify the bucket is reachable.
        await self.check()

    async def upload(self, local_path: str, name: str) -> None:
        client = self._get_client()
        key = self._key(name)
        try:
            await self._run(
                lambda: client.upload_file(local_path, self._bucket, key)
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, "upload") from exc

    async def download(self, name: str, local_path: str) -> int:
        client = self._get_client()
        key = self._key(name)
        try:
            await self._run(
                lambda: client.download_file(self._bucket, key, local_path)
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, "download") from exc
        return os.path.getsize(local_path)

    async def delete(self, name: str) -> None:
        client = self._get_client()
        key = self._key(name)
        try:
            await self._run(
                lambda: client.delete_object(Bucket=self._bucket, Key=key)
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, "delete") from exc

    async def list(self) -> List[RemoteFile]:
        client = self._get_client()
        list_prefix = f"{self._prefix}/" if self._prefix else ""

        def _list() -> List[RemoteFile]:
            files: List[RemoteFile] = []
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=self._bucket, Prefix=list_prefix
            ):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Strip the prefix and skip anything in a sub-folder or the
                    # "folder marker" key itself.
                    rel = key[len(list_prefix):] if list_prefix else key
                    if not rel or "/" in rel:
                        continue
                    modified = obj.get("LastModified")
                    if modified is not None and modified.tzinfo is None:
                        modified = modified.replace(tzinfo=timezone.utc)
                    files.append(
                        RemoteFile(
                            name=rel,
                            size=int(obj.get("Size", 0)),
                            modified=modified,
                            backend=self.name,
                        )
                    )
            return files

        try:
            return await self._run(_list)
        except Exception as exc:  # noqa: BLE001
            # A genuinely missing bucket is an error; an empty prefix is not.
            from botocore.exceptions import ClientError

            if isinstance(exc, ClientError):
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchBucket", "404", "NoSuchKey"):
                    return []
            raise self._map_error(exc, "list") from exc
