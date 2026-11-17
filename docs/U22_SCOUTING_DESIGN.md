# U22 scouting: phase 1 (transparent heuristic)

## Data
- /fixtures/players provides per-match minutes, goals, assists and provider rating.
- /players?id=...&season=... enriches verified player birth date, nationality and position.
- Players without verified DOB or available ratings are excluded from ranked U22 results.
- Empty, missing and malformed values remain **null**, not zero.
- Match ratings are **provisional** until a completed match status (FT, AET, PEN).
- A single provider call to fetch fixture player stats is charged against the daily free quota.
- Per-player profile calls also incur one request each; selectively enrich monitored prospects.

## API
- GET /api/v1/matches/{id}/players
- GET /api/v1/players/{id}
- GET /api/v1/scouting/u22?category=established&min_minutes=300
- GET /api/v1/scouting/u22?category=emerging&min_minutes=300

The under-22 test is **age less than 22 on the requested as_of date**.
Only completed fixtures up to as_of are considered. The categories separate
established >= minimum minutes and emerging 30..minimum-1 minutes.
Ratings are minutes-weighted. For sample-size shrinkage:
adjusted_rating = (sum(rating * minutes) + 6.5 * 270) / (rated_minutes + 270).
Scores are reported with raw average rating, minutes, matches, rated matches,
goal and assist rates, competition IDs and rating coverage.

## Honest limitations
This is NOT AI scouting or a prediction of future potential. It is a simple,
auditable candidate discovery baseline. Scores are not yet position- or
league-strength adjusted. Do not treat them as definitive comparisons across
Russian and Romanian leagues, or goalkeepers and forwards. No fabricated ratings,
birth dates, or results. Provider availability controls coverage.

## Manual imports
Apply migrations first.
- python -m app.players_cli fixture --match-id 1
- python -m app.players_cli profile --provider-player-id 123 --season 2026

The current scheduler does NOT continuously import player ratings/profiles.
This is deliberate to respect the free daily quota and prevent accidental costs.

## Next
Add multi-provider identity reconciliation, birth date completion strategy,
position+league baselines, goalkeeper and defender-specific metrics, event
correction reconciliation, job retry/backoff, favorites and idempotent notifications.
