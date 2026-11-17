# Provider readiness for a small FootScore pilot (v0.10)

## Separate two distinct checks
1. **Provider capability hint:** `GET /leagues?id=...&season=...` returns
   `seasons[].coverage.fixtures` booleans for events, player/fixture stats
   and lineups. This says that the provider reports *potential support*
   for those features, NOT that every fixture or player is complete.
2. **Observed local completeness:** historical backfill receipts show
   which locally known fixtures returned data, empty responses, or were not
   fetched. This does not prove the external season schedule is exhaustive.
Neither check alone establishes valid negative outcomes for ML training.

An unknown or missing capability is represented as `null`, never
automatically as `false`. Historical provider availability and data rights
depend on subscription plan and league/season.

## Optional metadata probe
```bash
alembic upgrade head
# Local-only cached view; consumes NO external API quota:
python -m app.pilot_cli --league 39 --season 2024

# One explicitly quota-reserved API-Football call:
python -m app.pilot_cli --league 39 --season 2024 --probe
```

The `--probe` command requires an environment `API_FOOTBALL_KEY`. No
credentials or real provider data are included in this repository.
After a successful HTTP response the declared feature flags are stored in
`league_coverage_probes`, not in `Competition` as a verified truth.

Next, import real fixtures for a season to be studied (one quota call),
then preview a bounded fixture-player backfill and check local receipts:
```bash
python -m app.cli --league 39 --season 2024
python -m app.historical_cli --competition-id 1 \
  --start 2024-07-01 --end 2025-06-30 --max-requests 5
python -m app.historical_cli --competition-id 1 \
  --start 2024-07-01 --end 2025-06-30 --report
```
`competition-id` is FootScore's internal ID, not API-Football's league ID.
These examples are not proof that historical data access is included on
the free plan.

## Provider-wide database request guardrails
All in-app API-Football calls now reserve against **one atomic transaction**:
- Daily budget with an explicit reserve (default 100 total, 10 held back).
- Rolling 60-second budget (default **8 requests**, below the 10/min
  provider policy reported for the free plan).
- One PostgreSQL lock row per provider serializes all in-app callers,
  avoiding races between scheduler, imports, player details and probes.
  The PostgreSQL server clock is sampled **after** obtaining that lock;
  otherwise blocked workers can undercount recent reservations.
- The reservation commits *before* HTTP and is not refunded on failures.
- A sliding window spans UTC minute boundaries; 8 at 12:00:59 cannot
  become another 8 at 12:01:00.
- If either budget is exhausted, the request is rejected locally before
  HTTP. No automatic retry or unlimited waiting.

Important limitations:
- This lock covers **only processes sharing the same FootScore database**.
  It cannot see other apps or API users on the same provider key, nor
  account-wide counters or provider-side rate decisions.
- The provider's real counter, resets, 429 handling and subscription
  entitlements remain authoritative. Do not claim account-wide compliance.
- SQLite is supported for local tests but not a multi-machine deployment.
- Request-log rows are retained for audit; scheduled pruning/archival or a
  retention policy is required before high-volume deployment.
- The free plan cannot support large-scale Flashscore-style constant polling.

## ML research gate
A positive provider feature flag and a locally successful response are **not**
a license to assign training labels. To train from real results, independently
verify season fixture listings, roster identity, player minutes (including
substitutions) and historical cutoff provenance. See
[Historical Backfill](HISTORICAL_BACKFILL.md) and
[ML Research Protocol](ML_RESEARCH_PROTOCOL.md).

## Sources
- https://www.api-football.com/documentation-v3
- https://www.api-football.com/pricing/
