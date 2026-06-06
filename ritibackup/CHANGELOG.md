# Changelog

All notable changes to RitiBackup are documented here.

## 1.2.0

- **Delete a single backup:** remove an individual backup from the backend it
  lives on, straight from the web UI.
- **Clear backups in bulk:** delete every backup on one backend — or on all
  enabled backends at once. This action is irreversible.
- **Upload a backup from your computer:** send a backup file from your PC to the
  selected backend(s) so you can restore from it afterwards. Both plain Home
  Assistant `.tar` and encrypted `.tar.riti` files are accepted; a plain tar can
  optionally be encrypted on-device before upload.

## 1.1.0

- **Multiple storage backends:** added **S3** (and S3-compatible providers such
  as MinIO and Wasabi) and **Backblaze B2** alongside WebDAV. Enable one or
  several backends; every backup is uploaded to all enabled backends.
- **Optional encryption:** on-device ChaCha20-Poly1305 encryption is now a
  switch and is **off by default**. With it off, backups are uploaded as plain
  Home Assistant tarballs (`.tar`); with it on, encrypted `.tar.riti`
  containers, exactly as before.
- **Per-backend retention:** the retention policy is applied independently to
  each enabled backend.
- **Restore source selection:** choose which backend to pull a backup from when
  restoring. Plain `.tar` backups restore without a passphrase.
- **Redesigned UI** for managing encryption and multiple backends.

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
