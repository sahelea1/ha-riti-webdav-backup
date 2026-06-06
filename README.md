# RitiBackup Add-on Repository

A Home Assistant add-on repository containing **RitiBackup** — encrypted Home
Assistant backups to WebDAV, with on-device ChaCha20-Poly1305 encryption, an
automatic schedule, smart retention, and one-click decrypt & restore.

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**.
2. Open the **⋮** menu (top right) → **Repositories**.
3. Paste this repository's URL and click **Add**:

   ```
   https://github.com/sahelea1/ha-riti-webdav-backup
   ```
4. Close the dialog, find **RitiBackup** in the store, and click **Install**.

Then open the add-on's **Documentation** tab (or [`ritibackup/DOCS.md`](ritibackup/DOCS.md))
for configuration and restore instructions.

## What's inside

| Path | Purpose |
| --- | --- |
| `ritibackup/` | The add-on (manifest, Docker build, app, docs) |
| `ritibackup/tools/riti-decrypt.py` | Standalone disaster-recovery decryptor (no Home Assistant needed) |

## Disaster recovery

Your encrypted backups are ordinary files on your WebDAV server and are not
locked to this add-on. On any machine with Python:

```bash
pip install cryptography
python3 ritibackup/tools/riti-decrypt.py YOUR_BACKUP.tar.riti
```

Enter your passphrase to get back a plain Home Assistant `.tar` you can upload
on a fresh install. Keep a copy of `riti-decrypt.py` together with your
passphrase.

## License

MIT — see [LICENSE](LICENSE).
