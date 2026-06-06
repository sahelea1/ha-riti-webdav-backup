#!/usr/bin/env python3
"""
riti-decrypt - standalone disaster-recovery decryptor for RitiBackup files.

This single file has NO dependency on the rest of the addon. The only third
party requirement is `cryptography` (pip install cryptography). It implements
exactly the same container format as the addon, so a *.tar.riti file pulled
from your WebDAV server can always be turned back into a plain Home Assistant
backup tar - even if Home Assistant itself is gone.

    Usage:
        riti-decrypt.py BACKUP.tar.riti [OUTPUT.tar]
        riti-decrypt.py --verify BACKUP.tar.riti
        riti-decrypt.py --info   BACKUP.tar.riti

    The passphrase is read from the RITI_PASSPHRASE environment variable if set,
    otherwise you are prompted for it (input hidden).

After decrypting you get an ordinary Home Assistant backup .tar which you can
restore from Settings -> System -> Backups -> Upload backup, or by dropping it
into the /backup folder of a new install.

Container layout (33-byte header, then ChaCha20-Poly1305 STREAM chunks):
    magic      8 bytes   b"RITIBKDV"
    version    1 byte    0x01
    kdf_id     1 byte    0x01  (scrypt)
    n_log2     1 byte    scrypt cost, N = 2**n_log2
    r          1 byte    scrypt block size
    p          1 byte    scrypt parallelism
    chunk_size 4 bytes   big-endian uint32 plaintext bytes per chunk
    salt       16 bytes  random KDF salt
    chunk_0..n each = ciphertext(plaintext_chunk) + 16-byte Poly1305 tag
The 33-byte header is authenticated as AAD on chunk 0; the final chunk's nonce
carries a 0x01 flag so truncation is detected.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
except ImportError:
    sys.stderr.write(
        "ERROR: the 'cryptography' package is required.\n"
        "       Install it with:  pip install cryptography\n"
    )
    sys.exit(2)

MAGIC = b"RITIBKDV"
VERSION = 1
KDF_SCRYPT = 1
SALT_LEN = 16
TAG_LEN = 16
HEADER_FORMAT = ">8sBBBBBI16s"
HEADER_LEN = struct.calcsize(HEADER_FORMAT)  # 33


class DecryptionError(Exception):
    pass


def _unpack_header(raw: bytes):
    if len(raw) != HEADER_LEN:
        raise DecryptionError("Truncated header")
    magic, version, kdf_id, n_log2, r, p, chunk_size, salt = struct.unpack(
        HEADER_FORMAT, raw
    )
    if magic != MAGIC:
        raise DecryptionError("Not a RitiBackup file (bad magic bytes)")
    if version != VERSION:
        raise DecryptionError("Unsupported container version %d" % version)
    if kdf_id != KDF_SCRYPT:
        raise DecryptionError("Unsupported KDF id %d" % kdf_id)
    if chunk_size == 0 or chunk_size > 64 * 1024 * 1024:
        raise DecryptionError("Invalid chunk size in header")
    return {
        "version": version,
        "kdf_id": kdf_id,
        "n_log2": n_log2,
        "r": r,
        "p": p,
        "chunk_size": chunk_size,
        "salt": salt,
    }


def _derive_key(passphrase: str, hdr) -> bytes:
    if not passphrase:
        raise DecryptionError("Passphrase must not be empty")
    kdf = Scrypt(salt=hdr["salt"], length=32, n=2 ** hdr["n_log2"], r=hdr["r"], p=hdr["p"])
    return kdf.derive(passphrase.encode("utf-8"))


def _nonce(counter: int, last: bool) -> bytes:
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


def decrypt(src, dst, passphrase: str) -> int:
    """Decrypt file object src into dst. Returns plaintext bytes written."""
    header_bytes = src.read(HEADER_LEN)
    hdr = _unpack_header(header_bytes)
    key = _derive_key(passphrase, hdr)
    aead = ChaCha20Poly1305(key)

    enc_chunk = hdr["chunk_size"] + TAG_LEN
    counter = 0
    written = 0
    prev = src.read(enc_chunk)
    if prev == b"":
        raise DecryptionError("Truncated file (no ciphertext)")

    try:
        while True:
            cur = src.read(enc_chunk)
            last = cur == b""
            aad = header_bytes if counter == 0 else None
            plain = aead.decrypt(_nonce(counter, last), prev, aad)
            dst.write(plain)
            written += len(plain)
            counter += 1
            if last:
                break
            prev = cur
    except DecryptionError:
        raise
    except Exception as exc:
        raise DecryptionError(
            "Authentication failed: wrong passphrase or corrupted/tampered file"
        ) from exc
    return written


def _read_passphrase() -> str:
    env = os.environ.get("RITI_PASSPHRASE")
    if env:
        return env
    import getpass

    return getpass.getpass("RitiBackup passphrase: ")


def _human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024.0:
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f PiB" % n


def cmd_info(path: str) -> int:
    with open(path, "rb") as fh:
        hdr = _unpack_header(fh.read(HEADER_LEN))
    size = os.path.getsize(path)
    print("File:        %s" % path)
    print("Size:        %s (%d bytes)" % (_human(size), size))
    print("Format:      RitiBackup container v%d" % hdr["version"])
    print("KDF:         scrypt N=2^%d r=%d p=%d" % (hdr["n_log2"], hdr["r"], hdr["p"]))
    print("Chunk size:  %s" % _human(hdr["chunk_size"]))
    print("Salt:        %s" % hdr["salt"].hex())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="riti-decrypt",
        description="Standalone decryptor for RitiBackup (*.tar.riti) files.",
    )
    ap.add_argument("input", help="path to the encrypted .tar.riti file")
    ap.add_argument(
        "output",
        nargs="?",
        help="output .tar path (default: input with .riti stripped)",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="only verify passphrase + integrity, write nothing",
    )
    ap.add_argument(
        "--info",
        action="store_true",
        help="print container header info and exit (no passphrase needed)",
    )
    args = ap.parse_args(argv)

    if not os.path.exists(args.input):
        sys.stderr.write("ERROR: no such file: %s\n" % args.input)
        return 2

    if args.info:
        try:
            return cmd_info(args.input)
        except DecryptionError as exc:
            sys.stderr.write("ERROR: %s\n" % exc)
            return 1

    passphrase = _read_passphrase()

    if args.verify:
        try:
            with open(args.input, "rb") as src:
                n = decrypt(src, open(os.devnull, "wb"), passphrase)
            print("OK - passphrase correct and file intact (%s plaintext)." % _human(n))
            return 0
        except DecryptionError as exc:
            sys.stderr.write("FAILED: %s\n" % exc)
            return 1

    out = args.output
    if not out:
        out = args.input[:-5] if args.input.endswith(".riti") else args.input + ".tar"
    if os.path.exists(out):
        sys.stderr.write("ERROR: output already exists: %s\n" % out)
        return 2

    try:
        with open(args.input, "rb") as src, open(out, "wb") as dst:
            n = decrypt(src, dst, passphrase)
    except DecryptionError as exc:
        sys.stderr.write("FAILED: %s\n" % exc)
        if os.path.exists(out):
            os.unlink(out)
        return 1

    print("Decrypted %s -> %s (%s)" % (args.input, out, _human(n)))
    print("You can now restore this .tar from Home Assistant: Settings -> System")
    print("-> Backups -> three-dot menu -> Upload backup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
