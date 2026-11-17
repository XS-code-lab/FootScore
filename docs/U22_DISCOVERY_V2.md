# U22 talent discovery v2 — explainable cohort baseline

This module is **not** trained AI, a projection of transfer value, a
forecast of career outcomes, or a betting model. It is a scouting shortlist
built from observed match ratings and provider statistics.

## APIs
- GET /api/v1/scouting/u22/discovery?category=emerging
- GET /api/v1/scouting/u22/discovery?category=established&competition_id=1
- GET /api/v1/scouting/u22/discovery?position=defender&min_cohort=5
- GET /api/v1/scouting/u22/reports/{player_id}

The original /api/v1/scouting/u22 remains unchanged for compatibility.

## Cohort controls
- Match must be FT/AET/PEN, with kickoff on/before `as_of`, within the
  preceding 365 days. No provisional live ratings or future games.
- Confirmed player date of birth must give age < 22 at `as_of`.
- A player is assessed per competition ID and broad role group:
  attacker, midfielder, defender, goalkeeper. No averaging across
  positions or leagues and **no global cross-league leaderboard**.
- Peer cohort can include adults to avoid measuring a group entirely against
  other young debuts. A peer must have >=270 rated minutes and >=70%
  rating coverage; at least five peers are required by default.
- A position is mapped from provider `games.position` when present, falling
  back to player profile. Unknown position is excluded.
- Empty or underpopulated peer cohorts return an *unranked* candidate with
  `relative_score=null`, not a fabricated score.

## Scoring formula
`raw_rating = sum(rating * minutes) / rated_minutes`

`mu = mean(raw_rating of established peers)`

`sd = max(population_sd(peer raw_rating), 0.35)`

`z = clamp((raw_rating - mu) / sd, -2.5, 2.5)`

`reliability = rated_minutes / (rated_minutes + 270)`

`relative_score = clamp(50 + 15*z*reliability, 0, 100)`

This score is a **peer-relative display index, not an estimated probability**
of becoming a top footballer. Missing provider ratings and poor coverage
cause `relative_score=null`. Sample confidence is a heuristic data-quantity
indicator `min(1, rated_minutes/900) * rating_coverage`, *not calibrated*
to future success.

## Additional features and signals
Goals, assists, shots on target, key passes, dribbles, tackles, interceptions
and saves per 90 are shown when >=70% of minutes have the metric. Unknowns
stay null, not zero. The features add context but do not influence the score;
future supervised learning requires verified historical outcomes and coverage.

Signals:
- rating_above_position_league_peers
- early_goal_involvement
- insufficient_rating_coverage
- insufficient_comparable_peers
- small_playing_sample

Signal thresholds are heuristics; results are not definitive discoveries.
The API exposes method/version, all observations, coverage, cohort size,
and a readable explanation for each player.

## Data and ethics
- API-Football free quotas continue to apply to fixture/player import.
  Discovery itself only reads the existing database, spending **0** API calls.
- A single provider's ratings are not automatically comparable across
  different competitions. League-strength adjustment requires separately
  licensed, defensible calibration data; currently **not implemented**.
- Position-only peer ratings do not control for opponents, team strength,
  playing style, keeper-specific metrics, appearance bias or data errors.
- Scouting unknown/minor players should be handled responsibly: no
  fabricated nationality, age, private data, personality or character claims.
- A first professional appearance can be highlighted for further review but
  cannot establish high future potential.
- Request-time full-history scans are appropriate only for a small pilot.
  Add indexes/materialized aggregates, pagination and benchmarked caching
  before scaling to large season histories.

## Next
Collect historical, outcome-labeled player seasons and licensed league
strength adjustments; validate a reproducible time-split, leakage-free model
before calling any prediction "AI." Evaluate calibration and performance
by age, league, role and sample size, and publish uncertainty and failure
cases. Keep this transparent baseline as a fallback.
