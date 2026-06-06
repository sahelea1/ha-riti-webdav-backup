# RitiBackup

![Supports aarch64][aarch64-shield]
![Supports amd64][amd64-shield]
![Supports armhf][armhf-shield]
![Supports armv7][armv7-shield]
![Supports i386][i386-shield]

Home Assistant backups to **WebDAV**, **S3**, and **Backblaze B2** (use one or
many) — with **optional** on-device **ChaCha20-Poly1305** encryption, an
automatic every-N-days schedule, per-backend smart retention (keep your recent
backups plus a fortnight archive), and one-click restore. A bundled standalone
tool lets you recover encrypted backups even without Home Assistant.

## Highlights

- ☁️ **Multiple backends** — WebDAV (Nextcloud, ownCloud, your NAS), **S3** and
  S3-compatible providers (MinIO, Wasabi), and **Backblaze B2**. Enable one or
  several; every backup uploads to all of them.
- 🔐 **Optional on-device encryption** — flip on ChaCha20-Poly1305 to encrypt
  backups before they leave the device, so your storage only ever holds
  ciphertext. Off by default.
- 🗝️ **ChaCha20-Poly1305 STREAM** with scrypt key derivation and a unique salt
  per file.
- ⏲️ **Automatic schedule** — every second day at 03:00 by default, fully
  configurable, with catch-up for missed runs.
- ♻️ **Per-backend smart retention** — keep the 4 newest backups plus one from
  ~2 weeks ago on each backend (configurable; optional "bridge" mode keeps the
  archive fresh).
- ↩️ **One-click restore** — download, decrypt, verify, then import or fully
  restore, all from a polished UI.
- 🛟 **Disaster recovery** — a dependency-light `riti-decrypt.py` recovers your
  backups on any machine with Python.

## Quick start

1. Add this repository to Home Assistant: **Settings → Add-ons → Add-on Store →
   ⋮ → Repositories**, paste the repository URL, **Add**.
2. Install **RitiBackup** and open its **Configuration** tab.
3. Enable at least one storage backend (WebDAV, S3, or B2) and fill in its
   details. Optionally turn on `encryption_enabled` and set a strong
   `encryption_passphrase` — **store that passphrase safely, it cannot be
   recovered.**
4. Start the addon and open the **Web UI**. Run **Backup now** to confirm
   everything works, then let the schedule take over.

See **DOCS.md** for full configuration, restore instructions, and the
disaster-recovery workflow.

## Why not just the built-in WebDAV backup?

Home Assistant can use WebDAV as a backup location and can encrypt backups, but
RitiBackup is for people who specifically want **ChaCha20-Poly1305** on-device
encryption, an **every-second-day schedule with a keep-4 + fortnight-archive**
retention policy, and a **standalone, HA-independent recovery tool** — packaged
with a focused UI around exactly that workflow.

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[armhf-shield]: https://img.shields.io/badge/armhf-yes-green.svg
[armv7-shield]: https://img.shields.io/badge/armv7-yes-green.svg
[i386-shield]: https://img.shields.io/badge/i386-yes-green.svg
