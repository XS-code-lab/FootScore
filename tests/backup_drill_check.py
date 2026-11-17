"""CI-only seed and verify PostgreSQL backup -> *separate* restore database.

This script deliberately fails unless the disposable integration CI flag is on.
No real player/account data, tokens or external services are used.
"""
import argparse
import os

from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import (
    AuthOneTimeToken, EmailDelivery, LiveUpdate, Match, User,
)

SENTINEL = "ci-backup-drill-synthetic-v1"
EMAIL = "synthetic-restore-test@example.invalid"
EMAIL_CIPHERTEXT = "ci-only-fake-encrypted-payload-not-a-real-code"


def main() -> None:
    parser = argparse.ArgumentParser(description="CI fixture backup/restore verifier")
    parser.add_argument("action", choices=["seed", "verify"])
    args = parser.parse_args()
    if os.getenv("FOOTSCORE_INTEGRATION_TESTS") != "1":
        parser.error("Requires disposable CI integration database")
    if engine.dialect.name != "postgresql":
        parser.error("Only disposable PostgreSQL CI database supported")

    with SessionLocal.begin() as db:
        revision = db.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        if revision != "0012_backup_alert_state":
            raise RuntimeError("Unexpected migration head")
        if args.action == "seed":
            if db.scalar(select(Match.id).where(
                Match.provider_id == SENTINEL
            )) is not None:
                raise RuntimeError("Sentinel already exists")
            match = Match(
                provider="ci-synthetic", provider_id=SENTINEL,
                status="FT", home_score=2, away_score=1,
            )
            user = User(email=EMAIL, password_hash="ci-only-not-a-real-hash")
            db.add_all([match, user])
            db.flush()
            db.add(LiveUpdate(
                match_id=match.id, kind="ci_restore_sentinel",
                payload={"value": 42, "synthetic_only": True},
            ))
            token = AuthOneTimeToken(
                user_id=user.id, purpose="verify_email",
                token_hash="c" * 64,
                created_at=match.observed_at,
                expires_at=match.observed_at,
            )
            db.add(token)
            db.flush()
            db.add(EmailDelivery(
                token_id=token.id,
                encrypted_code=EMAIL_CIPHERTEXT,
                status="pending", attempts=1,
                next_attempt_at=match.observed_at,
                created_at=match.observed_at,
            ))

            # A larger, deliberately synthetic snapshot exercises streaming
            # row-by-row verification without ever using customer data.
            extra_matches = [
                Match(
                    provider="ci-synthetic",
                    provider_id=f"ci-recovery-load-{i:04d}",
                    status="FT", home_score=i % 5, away_score=i % 3,
                )
                for i in range(180)
            ]
            db.add_all(extra_matches)
            db.flush()
            db.add_all([
                LiveUpdate(
                    match_id=item.id, kind="ci_load_update",
                    payload={
                        "match_index": index, "event_index": event,
                        "score": {"home": index % 5, "away": event % 3},
                    },
                )
                for index, item in enumerate(extra_matches)
                for event in range(8)
            ])
            extra_users = [
                User(
                    email=f"ci-recovery-load-{i:03d}@example.invalid",
                    password_hash="synthetic-hash-not-real",
                )
                for i in range(30)
            ]
            db.add_all(extra_users)
            db.flush()
            extra_tokens = [
                AuthOneTimeToken(
                    user_id=item.id, purpose="verify_email",
                    token_hash=f"{i + 1:064x}",
                    created_at=match.observed_at,
                    expires_at=match.observed_at,
                )
                for i, item in enumerate(extra_users)
            ]
            db.add_all(extra_tokens)
            db.flush()
            db.add_all([
                EmailDelivery(
                    token_id=item.id,
                    encrypted_code=f"ci-synthetic-ciphertext-{i}",
                    status="pending", attempts=1,
                    next_attempt_at=match.observed_at,
                    created_at=match.observed_at,
                )
                for i, item in enumerate(extra_tokens)
            ])
            print(
                "Seeded synthetic snapshot: 181 matches, 1441 match updates, "
                "31 accounts and 31 encrypted delivery records."
            )
        else:
            match = db.scalar(select(Match).where(
                Match.provider_id == SENTINEL,
            ))
            user = db.scalar(select(User).where(User.email == EMAIL))
            if match is None or user is None:
                raise AssertionError("Match/user lost during restore")
            assert (match.status, match.home_score, match.away_score) == (
                "FT", 2, 1,
            )
            update = db.scalar(select(LiveUpdate).where(
                LiveUpdate.match_id == match.id,
                LiveUpdate.kind == "ci_restore_sentinel",
            ))
            assert update is not None
            assert update.payload == {"value": 42, "synthetic_only": True}
            assert update.published_at is None
            token = db.scalar(select(AuthOneTimeToken).where(
                AuthOneTimeToken.user_id == user.id,
            ))
            assert token is not None and token.token_hash == "c" * 64
            delivery = db.scalar(select(EmailDelivery).where(
                EmailDelivery.token_id == token.id,
            ))
            assert delivery is not None
            assert delivery.encrypted_code == EMAIL_CIPHERTEXT
            assert delivery.status == "pending" and delivery.attempts == 1
            assert db.execute(text(
                "SELECT COUNT(*) FROM alembic_version"
            )).scalar_one() == 1
            print("Verified restored migration, match, user, replay, token and outbox.")


if __name__ == "__main__":
    main()
