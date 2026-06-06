# RitiBackup

<p align="center">
  <img src="https://img.shields.io/badge/aarch64-yes-green.svg" alt="aarch64">
  <img src="https://img.shields.io/badge/amd64-yes-green.svg" alt="amd64">
  <img src="https://img.shields.io/badge/armhf-yes-green.svg" alt="armhf">
  <img src="https://img.shields.io/badge/armv7-yes-green.svg" alt="armv7">
  <img src="https://img.shields.io/badge/i386-yes-green.svg" alt="i386">
</p>

Home Assistant backups to **WebDAV**, **S3**, and **Backblaze B2** — use one or
several at once — with **optional** on-device **ChaCha20-Poly1305** encryption,
an automatic every-N-days schedule, per-backend smart retention, and one-click
restore. A standalone tool lets you recover encrypted backups on any machine
with Python, no Home Assistant needed.

## Features

- ☁️ **Multiple backends** — WebDAV (Nextcloud, ownCloud, NAS), S3 / S3-compatible (MinIO, Wasabi), Backblaze B2. Enable one or all; every backup goes to every enabled backend.
- 🔐 **Optional on-device encryption** — ChaCha20-Poly1305 STREAM with scrypt key derivation. Off by default; flip one switch to protect all future backups.
- ⏲️ **Automatic schedule** — configurable interval (default: every 2 days at 03:00) with catch-up for missed runs.
- ♻️ **Per-backend smart retention** — keep N recent backups plus an archive copy on each backend. No manual cleanup needed.
- ↩️ **One-click restore** — pick a backup from the list, enter your passphrase, and the add-on handles everything: download, decrypt, verify, import.
- 🗂️ **Full backup management** — delete single backups, clear all backups, or upload a file from your PC to any backend.
- 🛟 **Disaster recovery** — `tools/riti-decrypt.py` decrypts backups on any machine with `pip install cryptography`.

## Quick start

1. Add this repository to Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, paste the repository URL, click **Add**.
2. Install **RitiBackup** and open its **Configuration** tab.
3. Enable at least one backend and fill in its credentials. Optionally enable encryption and set a passphrase — **store it safely; it cannot be recovered.**
4. Start the add-on and open the **Web UI**. Click **Backup now** to verify everything works, then let the schedule take over.

See [DOCS.md](DOCS.md) for the full configuration reference and restore instructions.
