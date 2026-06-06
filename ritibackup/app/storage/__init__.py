"""Pluggable storage backends for RitiBackup (WebDAV, S3, Backblaze B2).

Public API::

    from .storage import build_backends, StorageBackend, RemoteFile, StorageError

The concrete backend classes are also re-exported for convenience, but
:func:`build_backends` imports them lazily so importing this package never
requires optional dependencies (``boto3`` / ``b2sdk``).
"""

from __future__ import annotations

from .base import RemoteFile, StorageBackend, StorageError, build_backends

__all__ = [
    "StorageBackend",
    "RemoteFile",
    "StorageError",
    "build_backends",
]
