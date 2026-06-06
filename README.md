<p align="center">
  <img src="ritibackup/logo.png" alt="RitiBackup" width="192">
</p>

<h1 align="center">RitiBackup</h1>

<p align="center">
  <em>Home Assistant backups to WebDAV, S3, and Backblaze B2 —<br>with optional on-device encryption and one-click restore.</em>
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsahelea1%2Fha-riti-webdav-backup">
    <img src="https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg" alt="Add to Home Assistant">
  </a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/aarch64-yes-green.svg" alt="aarch64">
  <img src="https://img.shields.io/badge/amd64-yes-green.svg" alt="amd64">
  <img src="https://img.shields.io/badge/armhf-yes-green.svg" alt="armhf">
  <img src="https://img.shields.io/badge/armv7-yes-green.svg" alt="armv7">
  <img src="https://img.shields.io/badge/i386-yes-green.svg" alt="i386">
</p>

---

## Features

- ☁️ **Multiple storage backends** — WebDAV (Nextcloud, ownCloud, your NAS), S3 and S3-compatible (MinIO, Wasabi), and Backblaze B2. Enable one or all three; every backup uploads to every enabled backend simultaneously.
- 🔐 **Optional on-device encryption** — ChaCha20-Poly1305 STREAM construction with scrypt key derivation. Backups leave the device already encrypted; your storage only ever holds opaque ciphertext. Off by default.
- ⏲️ **Automatic schedule** — every N days at a configured time, with catch-up for missed runs while the add-on was off.
- ♻️ **Per-backend smart retention** — keep the N most-recent backups plus one archive copy from ~2 weeks ago on each backend independently. Optional bridge mode keeps the archive date fresh.
- ↩️ **One-click restore** — browse remote backups, click Restore, enter your passphrase (if encrypted), and choose import-only or full restore. The add-on handles download, decrypt, verify, and hand-off to the Supervisor.
- 🗂️ **Full backup management** — delete a single backup, clear all backups on one or every backend, or upload a backup file from your computer (plain `.tar` or encrypted `.tar.riti`) to any backend.
- 🛟 **Disaster recovery** — a standalone `riti-decrypt.py` recovers encrypted backups on any machine with Python and the `cryptography` package, no Home Assistant needed.

---

## Installation

1. Click **Add to Home Assistant** above, or go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories** and add:

   ```
   https://github.com/sahelea1/ha-riti-webdav-backup
   ```

2. Find **RitiBackup** in the store and click **Install**.
3. Open the add-on's **Configuration** tab, enable at least one storage backend, and fill in its credentials.
4. Optionally turn on `encryption_enabled` and set a strong `encryption_passphrase` — **write it down and store it safely; it cannot be recovered.**
5. Click **Start** and open the **Web UI** to run your first backup.

Full configuration reference and restore instructions: [`ritibackup/DOCS.md`](ritibackup/DOCS.md)

---

## What's inside

| Path | Purpose |
| --- | --- |
| `ritibackup/` | The add-on — config manifest, Dockerfile, app, docs |
| `ritibackup/tools/riti-decrypt.py` | Standalone disaster-recovery decryptor |

---

## Disaster recovery

Encrypted backups are ordinary files on your storage and are not locked to this add-on. On any machine with Python:

```bash
pip install cryptography
python3 ritibackup/tools/riti-decrypt.py YOUR_BACKUP.tar.riti
```

Enter your passphrase to get back a plain Home Assistant `.tar` that you can upload on a fresh install via **Settings → System → Backups → Upload backup**.

Keep a copy of `riti-decrypt.py` together with your passphrase.

---

## License

MIT — see [LICENSE](LICENSE).
