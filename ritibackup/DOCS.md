# RitiBackup

Encrypted Home Assistant backups to **WebDAV**, with on-device
**ChaCha20-Poly1305** encryption, an automatic schedule, smart retention, and
one-click decrypt & restore.

Your backups leave the device already encrypted. The WebDAV server (Nextcloud,
your NAS, a hosted box, anything that speaks WebDAV) only ever sees opaque
ciphertext. Only your passphrase can unlock them — and you can always unlock
them yourself, even without Home Assistant, using the bundled standalone tool.

---

## How it works

On each scheduled run RitiBackup:

1. Asks the Supervisor to create a **full** Home Assistant backup (the normal
   `.tar`).
2. **Encrypts** that tar on-device with ChaCha20-Poly1305 (streamed in chunks,
   so even multi-gigabyte backups never blow up memory).
3. **Uploads** the encrypted `*.tar.riti` file to your WebDAV server.
4. Deletes the local copies (the plain Supervisor backup and, by default, the
   local encrypted file) so nothing sensitive lingers on the device.
5. Applies **retention** on the WebDAV server, deleting backups you no longer
   want to keep.

A polished web UI (open it from the addon's **Open Web UI** button / sidebar
panel) lets you watch live progress, browse what's on the server, run a backup
on demand, and restore any backup with a couple of clicks.

---

## Configuration

Set these in the addon's **Configuration** tab.

### WebDAV

| Option | Meaning |
| --- | --- |
| `webdav_url` | Base URL of your WebDAV server, e.g. `https://nas.example.com/remote.php/dav/files/me` |
| `webdav_username` | WebDAV username |
| `webdav_password` | WebDAV password (use an app password where possible) |
| `webdav_path` | Folder on the server for backups (created if missing). Default `/RitiBackup` |
| `webdav_verify_ssl` | Verify the server's TLS certificate. Turn off only for self-signed certs you trust |

### Encryption

| Option | Meaning |
| --- | --- |
| `encryption_passphrase` | **The secret that protects every backup.** Choose a long, unique passphrase and store it somewhere safe (a password manager). **If you lose it, your backups are unrecoverable — by design.** |
| `kdf_n_log2` | scrypt cost as a power of two (N = 2^value). Default `16` (~64 MiB). Higher = slower to derive the key on every device, more resistant to brute force |
| `chunk_size_kib` | Streaming chunk size in KiB. Default `64`. Rarely needs changing |

### Schedule

| Option | Meaning |
| --- | --- |
| `schedule_time` | Time of day to run, `HH:MM` 24-hour, addon local time. Default `03:00` |
| `schedule_interval_days` | Run every N days. Default `2` (every second day) |
| `run_missed_on_start` | If the addon was off when a run was due, run once shortly after start |

### Retention

RitiBackup keeps a small, predictable set of backups on the server:

| Option | Meaning |
| --- | --- |
| `keep_recent` | Always keep this many newest backups. Default `4` |
| `archive_age_days` | Also keep one older "archive" backup closest to this age. Default `14` (about two weeks) |
| `keep_bridge` | Keep the chain of backups between the recent set and the archive, so the archive stays exactly ~14 days old instead of slowly ageing. Uses more storage. Default `false` |

With the defaults (`keep_recent: 4`, every 2 days, `archive_age_days: 14`) you
keep your **4 most recent** backups (covering the last ~8 days) **plus one
backup from about two weeks ago** — five files on the server at steady state.

> **Drift note:** with `keep_bridge: false` the single archived backup is not
> refreshed, so over time it ages past 14 days until a newer candidate becomes
> the closest match. If you want the archive to always be ~14 days old, set
> `keep_bridge: true` (this retains the in-between backups, so expect ~8 files).

### Housekeeping

| Option | Meaning |
| --- | --- |
| `backup_name_prefix` | Filename prefix on the server. Default `RitiBackup` |
| `delete_local_after_upload` | Remove the local encrypted copy after a successful upload. Default `true` |
| `compress_supervisor_backup` | Ask the Supervisor to gzip the backup before encryption. Default `true` |
| `log_level` | `trace`, `debug`, `info`, `notice`, `warning`, `error`, or `fatal` |

---

## Restoring

### From the UI (easiest)

1. Open the RitiBackup web UI.
2. In **Remote backups**, find the backup you want and press **Restore**.
3. Enter your passphrase. RitiBackup downloads the file, decrypts and verifies
   it on-device, and hands the plain backup to the Supervisor.
4. Choose either:
   - **Import only** — the backup appears in Home Assistant's normal Backups
     list so you can restore selectively, or
   - **Restore now** — the Supervisor performs a full restore immediately
     (Home Assistant will restart).

### Disaster recovery without Home Assistant

Your encrypted backups are just files on your WebDAV server, and they are
**not** locked to this addon. On any computer with Python and the
`cryptography` package:

```bash
pip install cryptography
python3 riti-decrypt.py RitiBackup_20260606T030000Z.tar.riti
```

You'll be prompted for your passphrase and get a plain Home Assistant
`*.tar` backup, which you can upload on a fresh install via
**Settings → System → Backups → Upload backup**.

Other modes:

```bash
python3 riti-decrypt.py --info    backup.tar.riti   # show header, no passphrase
python3 riti-decrypt.py --verify  backup.tar.riti   # check passphrase + integrity
python3 riti-decrypt.py backup.tar.riti out.tar     # explicit output path
```

`riti-decrypt.py` is bundled in the `tools/` folder of the addon repository.
Keep a copy alongside your passphrase.

---

## Security notes

- **Cipher:** ChaCha20-Poly1305 AEAD in a chunked STREAM construction (the same
  approach used by `age`). Every chunk is authenticated; the last chunk is
  flagged in its nonce so dropped/truncated data is detected.
- **Key derivation:** scrypt over your passphrase with a random 16-byte salt
  per file, so every backup file gets a unique key and the deterministic
  per-chunk nonce is always safe.
- **The header is authenticated** (used as additional data), so the encryption
  parameters can't be tampered with.
- The plaintext backup exists on the device only briefly during a run and is
  removed afterwards. Nothing readable is ever sent to the server.
- There is **no passphrase recovery**. That is the point of on-device
  encryption. Back up your passphrase separately.

---

## Troubleshooting

- **"Not fully configured" banner** — fill in the WebDAV URL/credentials and an
  encryption passphrase in the Configuration tab, then save and restart.
- **TLS errors** — if you use a self-signed certificate, set
  `webdav_verify_ssl: false` (only if you trust the network/server).
- **Connection test fails** — confirm the URL is the WebDAV *files* endpoint
  for your user, not the web login page. For Nextcloud it usually looks like
  `https://host/remote.php/dav/files/USERNAME`.
- **Restore doesn't start** — make sure you entered the exact passphrase used
  when the backup was created; use **Verify** in the standalone tool to check.
