"""Pluggable storage backend interface and backend factory.

Defines the common contract every storage backend (WebDAV, S3, B2) must
implement, plus :func:`build_backends` which inspects a ``Config`` object and
returns one backend instance per enabled backend.

The concrete backend modules are imported lazily inside :func:`build_backends`
so that importing this package never requires optional third-party libraries
(``boto3`` / ``b2sdk``) unless the corresponding backend is actually built.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class RemoteFile:
    """A single file stored in a remote backend.

    Attributes:
        name: Bare filename (no path/prefix), e.g. ``RitiBackup-2026.tar.gz``.
        size: Size in bytes.
        modified: Timezone-aware UTC datetime of last modification, or ``None``
            if the backend did not report one.
        backend: The owning backend's ``name`` (``"webdav"``/``"s3"``/``"b2"``).
    """

    name: str
    size: int
    modified: Optional[datetime]
    backend: str


class StorageError(Exception):
    """Raised for any storage backend failure with a human-readable message."""


class StorageBackend(abc.ABC):
    """Abstract base class for a pluggable backup storage backend.

    Concrete subclasses set the instance attributes :attr:`name` (a stable
    machine identifier) and :attr:`label` (a human-friendly display name) and
    implement the abstract async methods below.
    """

    #: Stable machine identifier, e.g. ``"webdav"``.
    name: str
    #: Human-friendly display name, e.g. ``"WebDAV"``.
    label: str

    @abc.abstractmethod
    async def check(self) -> None:
        """Verify connectivity and credentials.

        Raises:
            StorageError: If the backend is unreachable or credentials are bad.
        """

    @abc.abstractmethod
    async def ensure_ready(self) -> None:
        """Prepare the remote location (create dir/prefix) if the backend needs it.

        For object stores (S3/B2) prefixes are implicit, so this is a safe
        no-op or a light reachability check.

        Raises:
            StorageError: On failure to prepare the remote location.
        """

    @abc.abstractmethod
    async def upload(self, local_path: str, name: str) -> None:
        """Upload ``local_path`` storing it under the configured prefix as ``name``.

        Args:
            local_path: Path to the local file to upload.
            name: Bare filename to store it under (no path/prefix).

        Raises:
            StorageError: On upload failure.
        """

    @abc.abstractmethod
    async def download(self, name: str, local_path: str) -> int:
        """Download ``name`` from the configured prefix to ``local_path``.

        Args:
            name: Bare filename to fetch.
            local_path: Local destination path.

        Returns:
            The number of bytes written to ``local_path``.

        Raises:
            StorageError: On download failure.
        """

    @abc.abstractmethod
    async def delete(self, name: str) -> None:
        """Delete ``name`` from the configured prefix.

        Args:
            name: Bare filename to delete.

        Raises:
            StorageError: On delete failure.
        """

    @abc.abstractmethod
    async def list(self) -> List[RemoteFile]:
        """List all files directly under the configured prefix/dir.

        Returns:
            A list of :class:`RemoteFile` (with ``backend`` set to
            :attr:`name`); an empty list if the prefix/location is empty or
            missing.

        Raises:
            StorageError: On listing failure (other than a missing location).
        """


def build_backends(cfg) -> List[StorageBackend]:
    """Return one backend instance per *enabled* backend, in a fixed order.

    Order: WebDAV (``cfg.webdav_enabled``), S3 (``cfg.s3_enabled``),
    B2 (``cfg.b2_enabled``).

    Concrete backends are imported lazily so that this package never requires
    ``boto3``/``b2sdk`` unless the relevant backend is enabled.

    Args:
        cfg: A configuration object exposing the documented ``*_enabled`` and
            per-backend credential/prefix fields.

    Returns:
        A list of constructed :class:`StorageBackend` instances.
    """
    backends: List[StorageBackend] = []

    if getattr(cfg, "webdav_enabled", False):
        from .webdav import WebDavBackend

        backends.append(WebDavBackend(cfg))

    if getattr(cfg, "s3_enabled", False):
        from .s3 import S3Backend

        backends.append(S3Backend(cfg))

    if getattr(cfg, "b2_enabled", False):
        from .b2 import B2Backend

        backends.append(B2Backend(cfg))

    return backends
