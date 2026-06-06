"""
RitiBackup on-device encryption.

Backups are encrypted with ChaCha20-Poly1305 in a chunked STREAM construction
(Hoang-Reyhanitabar-Rogaway-Vizar, the same scheme used by `age`). This gives:

  * Symmetric authenticated encryption (AEAD) of arbitrarily large backups
    without ever holding the whole file in memory.
  * Per-chunk authentication tags, so a corrupted or tampered chunk is detected.
  * Truncation resistance: the final chunk carries a "last" flag in its nonce,
    so an attacker cannot silently drop trailing chunks.
  * Reorder resistance: the chunk counter is part of the nonce.

Key derivation uses scrypt (memory-hard) over a user passphrase plus a random
per-file salt, so every backup file gets a unique key. Because the key is unique
per file, the deterministic counter-based nonce is safe (no nonce reuse across
files under the same key).

Container layout (little of it is secret; the header is authenticated):

    magic        8 bytes   b"RITIBKDV"
    version      1 byte    0x01
    kdf_id       1 byte    0x01  (scrypt)
    n_log2       1 byte    scrypt cost, N = 2**n_log2
    r            1 byte    scrypt block size
    p            1 byte    scrypt parallelism
    chunk_size   4 bytes   big-endian uint32, plaintext bytes per chunk
    salt         16 bytes  random KDF salt
    --- 33-byte header above is used as AAD on chunk 0, binding all params ---
    chunk_0      plaintext_len + 16 (tag)
    chunk_1      ...
    chunk_n      final chunk (may be short, including empty)

The exact same format is implemented by the standalone tools/riti-decrypt.py so
backups can always be recovered without Home Assistant.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import BinaryIO

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"RITIBKDV"
VERSION = 1
KDF_SCRYPT = 1
SALT_LEN = 16
TAG_LEN = 16
HEADER_FORMAT = ">8sBBBBBI16s"  # magic, ver, kdf, n_log2, r, p, chunk_size, salt
HEADER_LEN = struct.calcsize(HEADER_FORMAT)  # 33 bytes

DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KiB plaintext per chunk
DEFAULT_N_LOG2 = 16             # scrypt N = 65536  (~64 MiB working set)
DEFAULT_R = 8
DEFAULT_P = 1


class DecryptionError(Exception):
    """Raised when authentication fails or the container is malformed."""


@dataclass(frozen=True)
class Header:
    version: int
    kdf_id: int
    n_log2: int
    r: int
    p: int
    chunk_size: int
    salt: bytes

    def pack(self) -> bytes:
        return struct.pack(
            HEADER_FORMAT,
            MAGIC,
            self.version,
            self.kdf_id,
            self.n_log2,
            self.r,
            self.p,
            self.chunk_size,
            self.salt,
        )

    @classmethod
    def unpack(cls, raw: bytes) -> "Header":
        if len(raw) != HEADER_LEN:
            raise DecryptionError("Truncated header")
        magic, version, kdf_id, n_log2, r, p, chunk_size, salt = struct.unpack(
            HEADER_FORMAT, raw
        )
        if magic != MAGIC:
            raise DecryptionError("Not a RitiBackup file (bad magic)")
        if version != VERSION:
            raise DecryptionError(f"Unsupported container version {version}")
        if kdf_id != KDF_SCRYPT:
            raise DecryptionError(f"Unsupported KDF id {kdf_id}")
        if chunk_size == 0 or chunk_size > 64 * 1024 * 1024:
            raise DecryptionError("Invalid chunk size")
        return cls(version, kdf_id, n_log2, r, p, chunk_size, salt)


def derive_key(passphrase: str, header: Header) -> bytes:
    if not passphrase:
        raise ValueError("Encryption passphrase must not be empty")
    kdf = Scrypt(
        salt=header.salt,
        length=32,
        n=2 ** header.n_log2,
        r=header.r,
        p=header.p,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _nonce(counter: int, last: bool) -> bytes:
    # 11-byte big-endian counter + 1-byte final flag = 12-byte nonce
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


def encrypt_stream(
    src: BinaryIO,
    dst: BinaryIO,
    passphrase: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    n_log2: int = DEFAULT_N_LOG2,
    r: int = DEFAULT_R,
    p: int = DEFAULT_P,
) -> None:
    """Encrypt `src` to `dst`. Streams in chunks; memory use is ~chunk_size."""
    header = Header(
        version=VERSION,
        kdf_id=KDF_SCRYPT,
        n_log2=n_log2,
        r=r,
        p=p,
        chunk_size=chunk_size,
        salt=os.urandom(SALT_LEN),
    )
    header_bytes = header.pack()
    key = derive_key(passphrase, header)
    aead = ChaCha20Poly1305(key)

    dst.write(header_bytes)

    counter = 0
    prev = src.read(chunk_size)
    if prev == b"":
        # Empty input -> single empty final chunk so the file is still valid.
        dst.write(aead.encrypt(_nonce(0, True), b"", header_bytes))
        return

    while True:
        cur = src.read(chunk_size)
        last = cur == b""
        aad = header_bytes if counter == 0 else None
        dst.write(aead.encrypt(_nonce(counter, last), prev, aad))
        counter += 1
        if last:
            break
        prev = cur


def decrypt_stream(src: BinaryIO, dst: BinaryIO, passphrase: str) -> None:
    """Decrypt and verify `src` to `dst`. Raises DecryptionError on any failure."""
    header_bytes = src.read(HEADER_LEN)
    header = Header.unpack(header_bytes)
    key = derive_key(passphrase, header)
    aead = ChaCha20Poly1305(key)

    enc_chunk = header.chunk_size + TAG_LEN
    counter = 0
    prev = src.read(enc_chunk)
    if prev == b"":
        raise DecryptionError("Truncated file (no ciphertext)")

    try:
        while True:
            cur = src.read(enc_chunk)
            last = cur == b""
            aad = header_bytes if counter == 0 else None
            dst.write(aead.decrypt(_nonce(counter, last), prev, aad))
            counter += 1
            if last:
                break
            prev = cur
    except Exception as exc:  # InvalidTag and friends
        raise DecryptionError(
            "Authentication failed: wrong passphrase or corrupted/tampered file"
        ) from exc


def encrypt_file(
    src_path: str,
    dst_path: str,
    passphrase: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    n_log2: int = DEFAULT_N_LOG2,
    r: int = DEFAULT_R,
    p: int = DEFAULT_P,
) -> int:
    """Encrypt a file path to a file path. Returns the encrypted size in bytes."""
    with open(src_path, "rb") as src, open(dst_path, "wb") as dst:
        encrypt_stream(
            src, dst, passphrase, chunk_size=chunk_size, n_log2=n_log2, r=r, p=p
        )
    return os.path.getsize(dst_path)


def decrypt_file(src_path: str, dst_path: str, passphrase: str) -> int:
    """Decrypt a file path to a file path. Returns the plaintext size in bytes."""
    with open(src_path, "rb") as src, open(dst_path, "wb") as dst:
        decrypt_stream(src, dst, passphrase)
    return os.path.getsize(dst_path)


def verify_file(src_path: str, passphrase: str) -> bool:
    """Decrypt to /dev/null to confirm passphrase + integrity without writing output."""
    with open(src_path, "rb") as src, open(os.devnull, "wb") as dst:
        decrypt_stream(src, dst, passphrase)
    return True
