# RitiBackup

![Supports aarch64][aarch64-shield]
![Supports amd64][amd64-shield]
![Supports armhf][armhf-shield]
![Supports armv7][armv7-shield]
![Supports i386][i386-shield]

Encrypted Home Assistant backups to **WebDAV** — on-device
**ChaCha20-Poly1305** encryption, an automatic every-N-days schedule, smart
retention (keep your recent backups plus a fortnight archive), and one-click
decrypt & restore. A bundled standalone tool lets you recover backups even
without Home Assistant.

## Highlights

- 🔐 **On-device encryption** — backups are encrypted before they leave the
  device. Your WebDAV server only ever stores ciphertext.
- 🗝️ **ChaCha20-Poly1305 STREAM** with scrypt key derivation and a unique salt
  per file.
- ☁️ **Any WebDAV server** — Nextcloud, ownCloud, your NAS, a hosted box.
- ⏲️ **Automatic schedule** — every second day at 03:00 by default, fully
  configurable, with catch-up for missed runs.
- ♻️ **Smart retention** — keep the 4 newest backups plus one from ~2 weeks ago
  (configurable; optional "bridge" mode keeps the archive fresh).
- ↩️ **One-click restore** — download, decrypt, verify, then import or fully
  restore, all from a polished UI.
- 🛟 **Disaster recovery** — a dependency-light `riti-decrypt.py` recovers your
  backups on any machine with Python.

## Quick start

1. Add this repository to Home Assistant: **Settings → Add-ons → Add-on Store →
   ⋮ → Repositories**, paste the repository URL, **Add**.
2. Install **RitiBackup** and open its **Configuration** tab.
3. Set your WebDAV URL, username, password, and a strong
   `encryption_passphrase`. **Store that passphrase safely — it cannot be
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
