# Historical football data collection and coverage audit (v0.9)

## Purpose
This is a small-pilot, quota-limited historical fixture-player import workflow
to prepare FootScore's U22 ML research data. **It does not verify that an
entire season is covered, nor does it produce trustworthy training labels.**

API-Football free access may restrict historical seasons, and statistical
coverage varies by competition, season and fixture. In its v3 documentation
the league `coverage.fixtures.statistics_players` flag means the *feature
may be available*; it does not certify every match or player is covered.
Never equate an empty `/fixtures/players` response with eleven or zero
participants, and never use a missing player row as a verified negative ML
outcome.

Provider references:
- https://www.api-football.com/documentation_v3
- https://www.api-football.com/pricing/
- https://www.api-football.com/news/post/how-to-get-all-data-from-a-league-with-api-football

## Setup
```bash
pip install -e '.[dev,ml]'
alembic upgrade head
```

First import *known, eligible* competition fixtures with the existing CLI.
Those are only **locally known fixtures**; do not assume the source response
lists every official match.

```bash
python -m app.cli --league 39 --season 2024
```

The free plan may not have access to the requested historical season.
Only use a league/season you have a right to retrieve. No data is
bundled and no provider calls happen by merely running an audit.

## Preview and execute a small batch
Use the LOCAL `competition_id`, not the provider league ID.
Specify an explicit kickoff date interval; use an interval appropriate to
the league's real season calendar:

```bash
python -m app.historical_cli --competition-id 1 \
  --start 2024-07-01 --end 2025-06-30 --max-requests 5
```

The default action **only prints a plan**. It does not use quota or
send any HTTP requests. Review it before execution.

```bash
python -m app.historical_cli --competition-id 1 \
  --start 2024-07-01 --end 2025-06-30 --max-requests 5 --execute
```

Execution requires `API_FOOTBALL_KEY`. Every fixture uses one
`/fixtures/players` provider request, with an atomic daily quota
reservation before the HTTP call. Requests are not refunded after
failures. At most 10 fixtures can be selected per invocation and
a 7-second delay is used between calls as a **local courtesy limit**;
other callers sharing the same key still need coordinated per-minute
limiting. Run only one backfill process and leave the daily reserve intact.

The planner checks both locally recorded daily and rolling-60-second
remaining request allowances, but these estimates are not authoritative
for other applications sharing the key. See [provider readiness](PILOT_PROVIDER_READINESS.md).

A successful nonempty response is recorded as `imported`, and a
successful empty or unrecognized response as `empty`. Neither is
certified complete. Empty responses are not retried automatically:
review the reason first, then opt into `--retry-empty`. Failures without
successful receipts remain eligible for a later explicit run.

## Local-only audit report
```bash
python -m app.historical_cli --competition-id 1 \
  --start 2024-07-01 --end 2025-06-30 --report
```

It counts locally known final fixtures, imported / empty / not fetched
player responses, imported player-record counts and receipt timestamps.

**This report cannot certify:**
- That provider season fixture IDs include the entire actual schedule.
- That every lineup, sub, player identity or minute was returned.
- That present-day corrected records were knowable at a historic cutoff.
- That a player absent from the database made zero appearances.
- That the provider has authorized redistribution or model training.

Independently licensed schedules, provider coverage comparison and manual
timestamped source audits are still necessary. The report always sets
`is_suitable_for_negative_ml_labels = false`.

## Inspect next-season outcome observations WITHOUT creating labels
```bash
mkdir -p data/ml
python -m app.ml.cli export --cutoff 2024-06-30 \
  --competition-id 1 --output data/ml/unverified.jsonl
python -m app.ml.cli propose-outcomes \
  --input data/ml/unverified.jsonl \
  --output data/ml/outcome-review.jsonl
```

The outcome-review file displays observed next-season same-league
minutes and a boolean `observed_threshold_reached` for the narrowly
defined 900-minute research target. A false boolean means *not reached
in the local archive* and never licenses a negative outcome label.
Even when 900 minutes have been observed, the training `target` remains
`null` until independently audited.

Both provenance flags remain `false` regardless of input declarations.
A reviewer must obtain original point-in-time feature evidence, confirm
the future season's complete roster/fixture/player-minute coverage, then
separately curate a verified JSONL dataset. See
[ML research protocol](ML_RESEARCH_PROTOCOL.md) for the exact gating rules.

## Limits
- No historical data access or API key is included in the repository.
- No automated mass historical polling, retraining or self-certification.
- No multi-provider reconciliation or end-to-end season completeness proof.
- A player may transfer midseason: a negative result in the original
  competition is NOT evidence of lower sporting potential.
- Keep the research data off Git: `data/ml/` is ignored.
