# Changelog

All notable changes to RitiBackup are documented here.

## 1.0.0

Initial release.

- On-device backup encryption with ChaCha20-Poly1305 in a chunked STREAM
  construction; scrypt key derivation with a unique per-file salt.
- WebDAV upload/download/list/delete with streamed transfers.
- Full Home Assistant backups via the Supervisor API.
- Automatic schedule (every N days at a fixed time) with catch-up for missed
  runs.
- Smart retention: keep the newest N backups plus one archive backup closest to
  a target age, with an optional "bridge" mode that keeps the archive fresh.
- Polished single-page web UI (Ingress): live job progress, retention timeline,
  remote backup browser, on-demand backup, and restore (import-only or full
  restore).
- One-click restore: download, decrypt, verify on-device, then hand off to the
  Supervisor.
- Standalone `tools/riti-decrypt.py` for disaster recovery without Home
  Assistant (`--info`, `--verify`, and decrypt modes), byte-for-byte compatible
  with the addon's container format.
