# FootScore U22 model research protocol (v0.8 — offline only)

This commit adds a **trainable, auditable machine-learning experiment**.
It does **not** contain any real trained model, verified historical
labels, calibrated performance estimate, or public ML prediction endpoint.

## Exact target, not vague "talent"
We measure one narrow, observable endpoint:

**`next_season_900_minutes_in_same_competition`:** whether a U22
player accumulated at least 900 recorded minutes in the **same tracked
competition** during the next fully verified July 1–June 30 season.

This is **NOT** talent, future transfer value, or global career success.
A transfer to another league can produce a legitimate negative outcome
for this narrow league-retention target while the player's career improves.

Do not create negative labels from absence of records in a free API sample.
Both outcomes require an independent audit that the full next-season
fixtures and player statistics for the particular competition were covered.
Player-ID continuity and roster changes must be reviewed.

## Feature snapshot contract
`python -m app.ml.cli export --cutoff 2024-06-30 --competition-id 1 --position attacker --output data/ml/unverified.jsonl`

Runs from the **local database**, with no paid/free external API calls.
It uses completed matches in the previous 365 days, and only U22 players
with verified birth date and at least 30 observed minutes.

Each JSONL row has a `feature_cutoff`, forward outcome window, ordered
feature schema and player/competition/position provenance. It intentionally
starts with `target: null`, `point_in_time_verified: false`,
`coverage_verified: false`, and empty label audit fields.

**Critical:** A database populated or corrected in 2026 can produce an
apparently 2024 snapshot from current values. That is *not* genuine
point-in-time evidence; historical features must be recovered from an
archived original snapshot or independently audited dated records.
Do **not** flip `point_in_time_verified` without that proof. Also note
that `Player.birth_date` and `Player.position` can be backfilled later;
you must verify their historical availability.

Feature order:
`age, minutes, matches, rated_minutes, average_rating, rating_coverage,
goals_per90, assists_per90, shots_on_target_per90, key_passes_per90,
successful_dribbles_per90, tackles_per90, interceptions_per90, saves_per90`.

Only explicitly available provider metrics are used; missing values
remain null. League/position identities and player IDs are audit fields,
**not fitted model features**. One model can be trained only for a
single competition + position group.

## Curating a verified label file
For each row, an auditor must:
1. Confirm the cutoff feature values were knowable by the cutoff date,
   with a timestamped archive or independently dated source.
2. Verify all next-season fixtures AND player-minute records are complete
   for the same competition. A free, partial API archive is insufficient.
3. Set `point_in_time_verified=true` and `coverage_verified=true`.
4. Set `target` to 0 or 1 based on the audited 900-minute threshold,
   `label_source` to an auditable dataset/version reference, and
   `label_verified_at` to a date AFTER the target-window end.
5. Do not use a label that was unavailable as of the earlier training
   decision date. Do not include future outcomes in feature fields.

To prepare human review of the NEXT season, use:
```bash
python -m app.ml.cli propose-outcomes --input data/ml/unverified.jsonl --output data/ml/outcome-review.jsonl
```
This records observed local next-season minutes and whether 900 has been
reached in the *local archive*, but never assigns any training label or
claims fixture/player coverage is complete. A zero may reflect missing
provider data rather than an actual nonappearance. Consult
[historical backfill documentation](HISTORICAL_BACKFILL.md).

No valid labels are fabricated by this repository. Neither this tool nor
users should treat simple missing match rows as a verified zero.

## Chronological holdout
```bash
pip install -e '.[dev,ml]'
python -m app.ml.cli train --input data/ml/verified.jsonl --output data/ml/experiment.json
```

The training command refuses data that:
- Mix competitions/positions or duplicate a player within a cutoff.
- Contains future or mismatched outcome windows.
- Omits auditing fields or includes unverified negative outcomes.
- Has training outcomes not verified before the newest cutoff.
- Has fewer than 30 train or 10 validation examples; or lacks both
  outcome classes in either set.

All earlier cutoffs train a median-imputed, standardized regularized
logistic regression; the latest cutoff is the **untouched chronological
holdout**. Means, medians and coefficients are learned **only on training
records**. It reports holdout ROC-AUC, average precision, Brier score
and the constant train-prevalence baseline Brier score. Sample size, split,
cohort and fingerprint are retained for audit.

Artifact is safe-to-inspect JSON coefficients; no pickled executables.
Every artifact is `approved_for_production=false`. No live inference API
is added because metrics cannot be trusted until real licensed historical
data and a rigorous independent review are available. The score function
in the research package is for offline replication only.

## Deployment and validity gates
- Audit historical as-of snapshots, data rights and cohort completeness.
- Use multiple seasons and multiple independent future-period holdouts;
  single-split estimates are not robust.
- Assess accuracy, calibration and false positives by position, age,
  league and minutes; guard against small sample size and survivor bias.
- Compare to naive baseline and scouting v2; calculate uncertainty bands.
- Define transfer-aware success targets separately before claiming talent.
- Only after a positive, reproducible evaluation design a gated,
  reviewed inference service, rollback and ongoing drift monitoring.

The current free API strategy is a pilot. Building more code does not
magically yield complete real historical coverage or trustworthy labels.
