"""Minimal async WebDAV client for backup storage (PUT/GET/PROPFIND/DELETE/MKCOL)."""

from __future__ import annotations

import logging
import os
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator, List, Optional
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree as ET

import aiohttp

log = logging.getLogger("ritibackup.webdav")

DAV_NS = "DAV:"
PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:resourcetype/><d:getcontentlength/><d:getlastmodified/><d:displayname/>"
    "</d:prop></d:propfind>"
)


@dataclass
class RemoteFile:
    name: str
    path: str               # full server path (decoded)
    size: int
    modified: Optional[datetime]
    is_dir: bool


class WebDavError(Exception):
    pass


class WebDavClient:
    def __init__(
        self,
        url: str,
        username: str = "",
        password: str = "",
        verify_ssl: bool = True,
    ) -> None:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise WebDavError(f"Invalid WebDAV URL: {url!r}")
        self.scheme = parsed.scheme
        self.host = parsed.netloc
        self.base_path = "/" + parsed.path.strip("/")
        if self.base_path == "/":
            self.base_path = ""
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._auth = (
            aiohttp.BasicAuth(username, password) if username else None
        )

    # --- url helpers -----------------------------------------------------
    def _ssl(self):
        if self.scheme != "https":
            return None
        if self.verify_ssl:
            return None  # default verification
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _full_url(self, remote_path: str) -> str:
        parts = (self.base_path + "/" + remote_path.strip("/")).split("/")
        encoded = "/".join(quote(p) for p in parts if p != "")
        return f"{self.scheme}://{self.host}/{encoded}"

    def _session(self) -> aiohttp.ClientSession:
        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=120)
        connector = aiohttp.TCPConnector(ssl=self._ssl())
        return aiohttp.ClientSession(
            auth=self._auth, timeout=timeout, connector=connector
        )

    # --- operations ------------------------------------------------------
    async def check(self) -> None:
        """Verify connectivity + credentials against the base collection."""
        async with self._session() as s:
            async with s.request(
                "PROPFIND",
                self._full_url(""),
                data=PROPFIND_BODY,
                headers={"Depth": "0", "Content-Type": "application/xml"},
            ) as r:
                if r.status in (401, 403):
                    raise WebDavError("Authentication failed (check username/password)")
                if r.status >= 400 and r.status != 404:
                    raise WebDavError(f"WebDAV server returned HTTP {r.status}")

    async def ensure_dir(self, remote_dir: str) -> None:
        """Create the target directory (and parents) if missing."""
        segments = [p for p in remote_dir.strip("/").split("/") if p]
        path = ""
        async with self._session() as s:
            for seg in segments:
                path = f"{path}/{seg}"
                async with s.request("MKCOL", self._full_url(path)) as r:
                    # 201 created, 405 already exists, 301/302 also acceptable
                    if r.status not in (201, 405, 301, 302):
                        if r.status in (401, 403):
                            raise WebDavError("Authentication failed creating directory")
                        log.debug("MKCOL %s -> %s", path, r.status)

    async def upload(self, local_path: str, remote_path: str) -> None:
        size = os.path.getsize(local_path)

        async def file_sender() -> AsyncIterator[bytes]:
            with open(local_path, "rb") as fh:
                while True:
                    chunk = fh.read(1024 * 256)
                    if not chunk:
                        break
                    yield chunk

        async with self._session() as s:
            async with s.put(
                self._full_url(remote_path),
                data=file_sender(),
                headers={"Content-Length": str(size)},
            ) as r:
                if r.status not in (200, 201, 204):
                    body = await r.text()
                    raise WebDavError(
                        f"Upload failed HTTP {r.status}: {body[:200]}"
                    )

    async def download(self, remote_path: str, local_path: str) -> int:
        async with self._session() as s:
            async with s.get(self._full_url(remote_path)) as r:
                if r.status != 200:
                    raise WebDavError(f"Download failed HTTP {r.status}")
                with open(local_path, "wb") as fh:
                    async for chunk in r.content.iter_chunked(1024 * 256):
                        fh.write(chunk)
        return os.path.getsize(local_path)

    async def delete(self, remote_path: str) -> None:
        async with self._session() as s:
            async with s.delete(self._full_url(remote_path)) as r:
                if r.status not in (200, 202, 204, 404):
                    raise WebDavError(f"Delete failed HTTP {r.status}")

    async def list_dir(self, remote_dir: str) -> List[RemoteFile]:
        url = self._full_url(remote_dir)
        async with self._session() as s:
            async with s.request(
                "PROPFIND",
                url,
                data=PROPFIND_BODY,
                headers={"Depth": "1", "Content-Type": "application/xml"},
            ) as r:
                if r.status == 404:
                    return []
                if r.status not in (207, 200):
                    raise WebDavError(f"List failed HTTP {r.status}")
                text = await r.text()
        return self._parse_propfind(text, remote_dir)

    def _parse_propfind(self, xml_text: str, remote_dir: str) -> List[RemoteFile]:
        files: List[RemoteFile] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise WebDavError(f"Bad PROPFIND XML: {exc}") from exc

        self_path = (self.base_path + "/" + remote_dir.strip("/")).rstrip("/")
        for resp in root.findall(f"{{{DAV_NS}}}response"):
            href_el = resp.find(f"{{{DAV_NS}}}href")
            if href_el is None or not href_el.text:
                continue
            href = unquote(urlparse(href_el.text).path)
            decoded_path = href.rstrip("/")
            # Skip the collection itself
            if decoded_path == self_path:
                continue

            propstat = resp.find(f"{{{DAV_NS}}}propstat")
            prop = propstat.find(f"{{{DAV_NS}}}prop") if propstat is not None else None
            is_dir = False
            size = 0
            modified = None
            if prop is not None:
                rt = prop.find(f"{{{DAV_NS}}}resourcetype")
                if rt is not None and rt.find(f"{{{DAV_NS}}}collection") is not None:
                    is_dir = True
                cl = prop.find(f"{{{DAV_NS}}}getcontentlength")
                if cl is not None and cl.text and cl.text.isdigit():
                    size = int(cl.text)
                lm = prop.find(f"{{{DAV_NS}}}getlastmodified")
                if lm is not None and lm.text:
                    try:
                        modified = parsedate_to_datetime(lm.text)
                        if modified.tzinfo is None:
                            modified = modified.replace(tzinfo=timezone.utc)
                    except (TypeError, ValueError):
                        modified = None
            name = decoded_path.rsplit("/", 1)[-1]
            files.append(
                RemoteFile(
                    name=name,
                    path=decoded_path,
                    size=size,
                    modified=modified,
                    is_dir=is_dir,
                )
            )
        return files
