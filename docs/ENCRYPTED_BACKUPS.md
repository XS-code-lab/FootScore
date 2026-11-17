# FootScore v0.19 — authenticated encryption for database archives

The optional `app.backup_crypto` helper encrypts PostgreSQL custom-format
archives with **AES-256-GCM**. It streams 1-MiB blocks rather than loading an
entire database into memory. All archive ciphertext is authenticated:
changing the content, authentication tag, or header causes verification
and restore to fail.

v0.19 introduced local encryption without a cloud scheduler. v0.20 adds an
optional, explicitly enabled S3-compatible upload command and supervised
watch loop. It still does NOT create a bucket, cloud account, secret manager,
remote retention policy or production disaster recovery. See
[remote backup scheduling](REMOTE_BACKUP_SCHEDULER.md).

## Independent encryption key

Generate 32 random bytes and securely store the **64 hex digits** as
`FOOTSCORE_BACKUP_KEY` in a secret manager:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

This key is completely **separate from** `JWT_SECRET`,
`EMAIL_QUEUE_KEY`, and database passwords. Never commit it, put it
in a command argument, or copy it into backup archives. Losing it makes
encrypted archives impossible to restore. Keep secure, encrypted key
escrow and a tested recovery procedure. Rotating this key does NOT
automatically re-encrypt older archives; preserve the matching old keys.

## Encrypt, verify, and decrypt

Use the existing PostgreSQL 16 custom-format backup script in a
permission-restricted temporary directory, then immediately encrypt:

```bash
# After setting PGHOST, PGUSER, PGDATABASE and password via a secure method:
sh scripts/postgres_backup.sh /secure/tmp/footscore.dump

# FOOTSCORE_BACKUP_KEY must be set securely in the server environment:
python -m app.backup_crypto encrypt \
    --input /secure/tmp/footscore.dump \
    --output /secure/archives/footscore.dump.fsbak

python -m app.backup_crypto verify \
    --input /secure/archives/footscore.dump.fsbak

# Remove plaintext temporary dump after encrypted archive is verified.
rm -f /secure/tmp/footscore.dump
```

The encrypted file has a short versioned header, 12-byte random nonce,
ciphertext, and 16-byte GCM authentication tag. Files are written
to a mode-0600 temporary file and linked atomically to a **new**
destination. The tool refuses to overwrite existing files or read
symlink input files. Verification checks authentication with **no
plaintext output file**. All normal data is encrypted and integrity
protected, but the header and total approximate archive size remain
visible; this is not traffic-pattern protection.

To restore, decrypt to a NEW private path only after obtaining the
matching key, then use the isolated restore helper:

```bash
python -m app.backup_crypto decrypt \
    --input /secure/archives/footscore.dump.fsbak \
    --output /secure/tmp/restore-input.dump

sh scripts/postgres_restore_drill.sh \
    /secure/tmp/restore-input.dump footscore_restore_test

rm -f /secure/tmp/restore-input.dump
```

A wrong key or modified ciphertext causes the decrypt operation to
fail and deletes its incomplete temporary plaintext. A successful
verification authenticates the archive **bytes**; it does not prove
the database is internally valid or that all important user data is
recoverable. Run a separate real restore and application check
regularly, on isolated non-production infrastructure.

## Threat model and caveats

- **Unauthenticated old backups:** plaintext `.dump` files from v0.18
  remain plaintext and must be protected or destroyed under your
  retention policy. v0.19 does not automatically convert any archive.
- **Temporary cleartext:** PostgreSQL `pg_dump` and the restore process
  still use an unencrypted private temporary dump. Deleting it with
  `rm` is not secure erasure from SSDs, snapshots, backups or storage
  journals. Place temporary files on properly encrypted scratch storage
  and exclude them from snapshot/remote log upload.
- **Key compromise:** anyone with both an encrypted archive and
  `FOOTSCORE_BACKUP_KEY` can read its contents. Use secret-manager
  access policies, encrypted offsite storage and hardware-backed keys
  if required by your threat model.
- **Identity secrets:** independent JWT and email queue keys are NOT
  embedded in the archive and must also be recoverable to operate
  the restored instance.
- **No overwrite:** output files must not exist, including dangling
  symlinks. This protects existing backups/restores from accidental
  clobbering.
- **No in-place encryption:** plaintext input stays untouched unless
  an explicit operator deletes it. The tool never deletes a database.
- **No cloud storage or schedule:** no backup jobs or retention policy
  are silently enabled.
- **Memory:** the stream uses at most a chunk per operation instead
  of holding the full dump in RAM.

## CI verification

The disposable PostgreSQL 16 integration job creates a real custom
archive from **synthetic** match, user, security-token and outbox data;
encrypts it with a **test-only** key; validates authentication; verifies
that a tampered encrypted file fails; removes the original plain dump;
decrypts to a private temporary file; and restores into a separate
disposable database. Restored data and migration revision are checked
independently, and existing target databases cannot be overwritten.

The separate unit tests cover multi-megabyte streaming, file
permissions, wrong-key rejection, header/tag/ciphertext tampering,
existing-output refusal, unsafe symlinks, and truncated archives.

See [backup/restore drill](BACKUP_RESTORE_DRILL.md) and
[operations monitoring](OPS_MONITORING.md). None of these tests
constitute production recovery certification or guarantee RPO/RTO.
