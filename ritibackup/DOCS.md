# RitiBackup

Home Assistant backups to **WebDAV**, **S3**, and **Backblaze B2** — use one or
several at once — with **optional** on-device **ChaCha20-Poly1305** encryption,
an automatic schedule, per-backend smart retention, and one-click restore.

Enable as many storage backends as you like; every backup is uploaded to all of
them. With encryption turned on, backups leave the device already encrypted and
your storage only ever sees opaque ciphertext that only your passphrase can
unlock (even without Home Assistant, via the bundled standalone tool).

---

## How it works

On each scheduled run RitiBackup:

1. Asks the Supervisor to create a **full** Home Assistant backup (the normal
   `.tar`).
2. If encryption is enabled, **encrypts** that tar on-device with
   ChaCha20-Poly1305 (streamed in chunks, so even multi-gigabyte backups never
   blow up memory) into a `*.tar.riti` container. If encryption is off, the
   plain `*.tar` is uploaded as-is.
3. **Uploads** the file to **every enabled backend** (WebDAV, S3, B2).
4. Deletes the local copies (the plain Supervisor backup and, by default, any
   temporary encrypted file) so nothing lingers on the device.
5. Applies **retention** independently on **each** backend, deleting backups you
   no longer want to keep.

A polished web UI (open it from the addon's **Open Web UI** button / sidebar
panel) lets you watch live progress, browse what's on each backend, run a backup
on demand, and restore any backup — picking which backend to pull it from — with
a couple of clicks.

---

## Storage backends

Enable **one or more** backends. Every backup is uploaded to **all enabled**
backends, and retention is applied to each backend separately. At least one
backend must be enabled before backups can run.

## Configuration

Set these in the addon's **Configuration** tab.

### Encryption (optional)

| Option | Meaning |
| --- | --- |
| `encryption_enabled` | Turn on-device ChaCha20-Poly1305 encryption on or off. **Default `false`.** When **off**, backups are uploaded as plain Home Assistant tarballs — anyone with access to the storage can read them. Turn it **on** for zero-knowledge storage |
| `encryption_passphrase` | Required when encryption is enabled. **The secret that protects every backup.** Choose a long, unique passphrase and store it somewhere safe (a password manager). **If you lose it, your encrypted backups are unrecoverable — by design.** |
| `kdf_n_log2` | scrypt cost as a power of two (N = 2^value). Default `16` (~64 MiB). Higher = slower to derive the key on every device, more resistant to brute force |
| `chunk_size_kib` | Streaming chunk size in KiB. Default `64`. Rarely needs changing |

> **Unencrypted backups are readable on the server.** If you leave
> `encryption_enabled` off, treat your storage as trusted — the `.tar` files are
> ordinary Home Assistant backups with no protection beyond your storage
> provider's access controls.

### WebDAV

| Option | Meaning |
| --- | --- |
| `webdav_enabled` | Enable the WebDAV backend. Default `false` |
| `webdav_url` | Base URL of your WebDAV server, e.g. `https://nas.example.com/remote.php/dav/files/me` |
| `webdav_username` | WebDAV username |
| `webdav_password` | WebDAV password (use an app password where possible) |
| `webdav_path` | Folder on the server for backups (created if missing). Default `/RitiBackup` |
| `webdav_verify_ssl` | Verify the server's TLS certificate. Turn off only for self-signed certs you trust |

### S3 (and S3-compatible)

| Option | Meaning |
| --- | --- |
| `s3_enabled` | Enable the S3 backend. Default `false` |
| `s3_endpoint_url` | Custom endpoint for S3-compatible providers (MinIO, Wasabi, etc.). Blank = AWS S3 |
| `s3_region` | Bucket region, e.g. `us-east-1` (optional for some providers) |
| `s3_bucket` | Bucket name where backups are stored |
| `s3_access_key_id` | Access key ID |
| `s3_secret_access_key` | Secret access key |
| `s3_prefix` | Key prefix (folder) within the bucket. Default `RitiBackup` |
| `s3_path_style` | Use path-style addressing. Required by some S3-compatible servers (e.g. MinIO). Default `false` |

### Backblaze B2

| Option | Meaning |
| --- | --- |
| `b2_enabled` | Enable the Backblaze B2 backend. Default `false` |
| `b2_key_id` | Application key ID (keyID) |
| `b2_application_key` | Application key secret |
| `b2_bucket` | Bucket name where backups are stored |
| `b2_prefix` | Key prefix (folder) within the bucket. Default `RitiBackup` |

### Schedule

| Option | Meaning |
| --- | --- |
| `schedule_time` | Time of day to run, `HH:MM` 24-hour, addon local time. Default `03:00` |
| `schedule_interval_days` | Run every N days. Default `2` (every second day) |
| `run_missed_on_start` | If the addon was off when a run was due, run once shortly after start |

### Retention

RitiBackup keeps a small, predictable set of backups. The policy is applied
**independently to each enabled backend**, so every backend ends up with the
same retained set:

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
| `delete_local_after_upload` | Remove the local Supervisor backup after a successful upload. Default `true` |
| `compress_supervisor_backup` | Ask the Supervisor to gzip the backup. Default `true` |
| `log_level` | `trace`, `debug`, `info`, `notice`, `warning`, `error`, or `fatal` |

---

## Managing backups

The **Remote backups** section of the web UI lists every backup across your
enabled backends. Beyond restoring, you can delete, bulk-clear, and upload
backups.

### Delete a single backup

Each entry has a **Delete** action that removes that one file from the backend
it lives on (shown next to each entry). Other backends and other backups are
untouched. The deletion is permanent.

### Clear all backups

Use **Clear** to delete **every** RitiBackup backup on a backend — pick a single
backend, or choose **all enabled backends** to wipe them everywhere at once.

> **This is irreversible.** Cleared backups cannot be recovered. Only files that
> match RitiBackup's naming pattern (`.tar` / `.tar.riti`) are removed; unrelated
> files in the same folder are left alone. If one backend fails, the others are
> still cleared and the error is reported.

### Upload a backup from your computer

Use **Upload** to send a backup file from your PC to one or more backends so it
appears in the list and can be restored later. This is handy for migrating
backups onto new storage or seeding a fresh install.

- **Accepted files:** both plain Home Assistant `.tar` backups and encrypted
  RitiBackup `.tar.riti` containers. RitiBackup detects an already-encrypted file
  automatically (by its `.tar.riti` name or its container header).
- **Choose the destination:** select which enabled backend(s) to send the file
  to (or all of them).
- **Optional encryption:** when you upload a plain `.tar`, you can ask
  RitiBackup to encrypt it on-device before upload (this requires an encryption
  passphrase to be configured). Files that are already encrypted are uploaded
  as-is.
- The file is streamed to disk and uploaded without being loaded into memory, so
  large backups work fine. Progress is shown in the live job panel.

Once uploaded, the backup appears in **Remote backups** and can be restored like
any other.

---

## Restoring

### From the UI (easiest)

1. Open the RitiBackup web UI.
2. In **Remote backups**, find the backup you want (each entry shows which
   backend it lives on) and press **Restore**.
3. For an encrypted backup (`.tar.riti`), enter your passphrase. RitiBackup
   downloads the file from the selected backend, decrypts and verifies it
   on-device, and hands the plain backup to the Supervisor. Plain `.tar`
   backups need no passphrase and are imported directly.
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

- **"Not fully configured" banner** — enable at least one storage backend and
  fill in its required fields (and, if encryption is enabled, a passphrase) in
  the Configuration tab, then save and restart.
- **TLS errors** — if you use a self-signed certificate, set
  `webdav_verify_ssl: false` (only if you trust the network/server).
- **Connection test fails** — confirm the URL is the WebDAV *files* endpoint
  for your user, not the web login page. For Nextcloud it usually looks like
  `https://host/remote.php/dav/files/USERNAME`.
- **Restore doesn't start** — make sure you entered the exact passphrase used
  when the backup was created; use **Verify** in the standalone tool to check.
