# Favorites and Notifications (Backend v0.5)

## Implemented
- JWT bearer access token (HS256, exp/iat/issuer/audience checks) and
  Argon2 password hashes; secure JWT_SECRET is mandatory for token issuance.
- Case-insensitive normalized email registration and password sign-in.
- User-scoped player favorites, preferences and notification inbox.
- Goal/assist notifications generated only for *new* fixture events.
- Final player rating notifications generated only once per match/player
  when status is FT/AET/PEN and a provider rating exists.
- Filtering by user preference and optional minimum final rating.
- Database unique index `(user_id, event_key)` prevents repeat inbox alerts.
- Device registration for optional FCM push and per-device outbox records.
- Manual at-least-once FCM dispatcher with bounded retries and error types.

## Setup
Set `JWT_SECRET` to a random value of at least 32 characters:
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```
Store it in the environment, not Git. Run `alembic upgrade head`.
Auth endpoints take JSON bodies (not OAuth2 form bodies).
Optional FCM requires `pip install -e '.[push]'`, configured Firebase
Admin credentials on worker only, and a client-provided device token.
Trigger `python -m app.push_cli` from a separate trusted worker.

## Endpoints
- POST /api/v1/auth/register
- POST /api/v1/auth/login
- GET /api/v1/auth/me
- GET /api/v1/favorites/players
- PUT /api/v1/favorites/players/{player_id}
- DELETE /api/v1/favorites/players/{player_id}
- GET/PATCH /api/v1/notifications/preferences
- POST/DELETE /api/v1/notifications/devices
- GET /api/v1/notifications?unread_only=true
- POST /api/v1/notifications/{notification_id}/read

## Limitations and safety
This is a **development baseline**, not production-ready identity security:
no refresh-token rotation, email verification, password reset, brute-force
rate limit, user deletion, audit log, worker lease/locking or endpoint abuse
defenses. Tokens and Firebase credentials must never be committed to Git.

No FCM send is possible until Firebase credentials and a client device token
are provided. Existing notifications are stored regardless of push setup.
Push delivery is at least once, not exactly once; a worker crash between
send and DB commit may generate duplicate pushes. Do not start multiple
push dispatchers until queue claiming is implemented.

Providers may edit/revoke goals and assists (including VAR decisions). Match
event fingerprint corrections/retractions are **not reconciled yet**; an
early alert may later be invalidated. No sending is performed from a
public HTTP endpoint. Database writes happen in the same transaction
as ingestion; failures roll back notifications with their source events.

Before launch, add event correction reconciliation, notification suppression
for unresolved VAR, push delivery observability, cleanup of invalid FCM
registration tokens, rate-limits, account lifecycle, secrets management and
controlled automated worker schedules.
