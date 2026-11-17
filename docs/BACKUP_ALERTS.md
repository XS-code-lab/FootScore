# FootScore backend v0.22 — opt-in backup-health webhook alerts

This release introduces **operator-configured HTTPS backup visibility alerts**.
Nothing is automatically scheduled or sent as part of the API deployment.
No real AWS S3 bucket or webhook account has been used in CI.

## What is monitored

The worker calls the existing **read-only** S3 inventory inspection
(`ListObjectsV2`). A qualifying backup must have a plausible FootScore
generated object name, creation time, size and S3 LastModified.

Conditions and outgoing events:

| Condition | Webhook event |
| --- | --- |
| No qualifying object | `backup_missing` |
| Newest candidate older than 30 hours | `backup_stale` |
| Inventory listing fails or is incomplete | `backup_inventory_unavailable` |
| Healthy *visibility* after any previously alerted failure | `backup_visibility_restored` |

**A recent object is NEVER evidence of a valid encrypted archive or a
successful PostgreSQL restore. For a separate, operator-run restore audit
use the v0.23 `app.restore_audit` CLI (ISOLATED_RESTORE_AUDIT.md).** All webhook payloads explicitly include
`cryptographic_integrity_verified=false` and `restore_verified=false`.
The recovery event describes visibility only, not database recoverability.

The payload includes an event name, severity, UTC observation time and the
two false verification indicators. It deliberately excludes bucket names,
S3 object keys, user identifiers, email addresses, passwords, access tokens,
cloud credentials and webhook URLs.

## Operator setup and explicit activation

Required:
- A trusted runtime with the FootScore source, installed
  `pip install -e '.[backup]'`, and S3 SDK credentials that are authorized
  **only to list the chosen bucket/prefix**.
- PostgreSQL with Alembic migration `0012_backup_alert_state` applied.
  This creates a small `backup_alert_states` table keyed by a SHA-256
  digest of bucket and prefix; no raw cloud identifier or webhook URL is
  stored in this table.
- An approved external HTTPS webhook that accepts POST requests with JSON,
  is reachable via port 443 and returns a 2xx status. Supply it through a
  **private secret manager environment variable**, never Git or CLI args:

```bash
export BACKUP_ALERT_WEBHOOK_URL='https://your-approved-webhook.example/path'
```

Run a **read-only preview**; it still performs a real S3 listing but
does not send a webhook or write database state:

```bash
python -m app.backup_alert_cli --bucket YOUR_PRIVATE_BUCKET
```

Send alerts and persist deduplication state only after explicit opt-in:

```bash
python -m app.backup_alert_cli \
  --bucket YOUR_PRIVATE_BUCKET --execute
```

To supervise an operator-selected recurring check, on **one dedicated
worker** (not the API process):

```bash
python -m app.backup_alert_cli \
  --bucket YOUR_PRIVATE_BUCKET --execute --watch \
  --interval-minutes 60 --remind-hours 24 --max-age-hours 30
```

Allowed worker intervals: 5–1440 minutes. Reminder intervals: 1–168
hours. This is a long-running process; deploy your own service supervisor
and egress rules. No task is created or started by FootScore just because
the code is present. A one-off inventory outage returns a nonzero exit
status (and, when opted in, tries the outage webhook); the supervised
worker can retry on its next scheduled check.

## Duplicate suppression and delivery guarantees

PostgreSQL workers share a state row per bucket/prefix, claimed via
`INSERT ... ON CONFLICT DO NOTHING` and `SELECT FOR UPDATE`.
Only the first worker observing a new condition sends the webhook.
While a bad condition persists, it sends a reminder at most once per
configured interval. When a previously alerted bad condition becomes
`recent_unverified`, it sends a visibility recovery event.

The HTTP request uses a bounded 5-second timeout, does not follow
redirects, ignores environment proxies and requires a trusted HTTPS
hostname on port 443. Literal IP addresses, localhost and common
private suffixes are rejected. This does not replace firewalls,
approved DNS allowlists or egress policies; DNS rebinding is still a
deployment concern. Webhook URL contents and exception details are
never printed into generic worker logs.

The database state is committed **after** the webhook returns a successful
2xx status. If it fails, the SQL transaction rolls back and a future
run can retry. However, webhook acceptance and SQL commit cannot be made
atomic: a process crash after HTTP 2xx and before the database commit may
cause **duplicate notifications**. Delivery is at-least-once attempted,
not exactly-once or guaranteed downstream receipt. The state table
does not contain a full incident/audit history.

## Scope and safeguards

- No backup is modified, deleted or automatically expired.
- No automatic external webhook configuration or S3 permissions exist.
- A failed or partial S3 list is classified as an alarm, never green.
- An inventory/report status is a statement about **metadata visibility**,
  not genuine historical completeness, backup integrity, cloud retention
  security, RPO/RTO, or restore success.
- SQL locking may keep the row locked for up to one HTTP timeout;
  use a bounded worker count and one supervised worker as the default.
- The database itself must be healthy to suppress duplicates. If it is
  down, alert state cannot be persisted; use a separate infrastructure
  monitor for PostgreSQL outages and backup-job execution failure.
- The webhook receiver should apply its own deduplication, monitoring,
  rate controls and secret rotation policy. This module does not validate
  receiver-side authentication/delivery after HTTP acceptance.

## CI evidence

Unit tests cover missing/stale/inventory failure, recovery, 24-hour
reminders, invalid/unsafe URLs, sanitized HTTP failure, state rollback
on refused webhooks, no-notification preview and no bucket details in
outgoing messages.

The disposable PostgreSQL CI job races four workers against one
backup scope and verifies one alert, followed by one visibility recovery.
The usual synthetic PostgreSQL archive encryption, mock S3 transfer and
isolated restore tests continue independently.

**No real webhook was called, no real S3 inventory was read and no cloud
backup was deleted.** The PR remains Draft pending an operational pilot.
