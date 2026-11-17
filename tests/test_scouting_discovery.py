"""U22 discovery v2: cohort isolation, missing-data honesty and API contracts."""
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Competition, Match, Player, PlayerMatchStats
from app.services.scouting_discovery import (
    _number, discover_u22, position_group,
)


AS_OF = date(2026, 11, 17)


def _seed(db: Session) -> dict:
    db.add_all([
        Competition(name="League One", provider="api-football", provider_id="100"),
        Competition(name="League Two", provider="api-football", provider_id="200"),
    ])
    db.flush()
    competitions = {
        row.provider_id: row.id for row in db.query(Competition).all()
    }
    # Three completed matches within the same lookback window, plus an
    # in-progress, future, and expired match to test temporal exclusion.
    matches = {}
    for external_id, league_id, day, status, year in (
        (1, "100", 10, "FT", 2026),
        (2, "100", 11, "FT", 2026),
        (3, "100", 12, "FT", 2026),
        (4, "200", 10, "FT", 2026),
        (5, "100", 16, "1H", 2026),
        (6, "100", 19, "FT", 2026),
        (7, "100", 10, "FT", 2024),
    ):
        match = Match(
            provider="api-football", provider_id=str(external_id),
            competition_id=competitions[league_id],
            kickoff=datetime(year, 11, day, 18, tzinfo=timezone.utc),
            status=status,
        )
        db.add(match)
        db.flush()
        matches[external_id] = match.id

    players = {}
    for external_id, name, born, pos in [
        *((pid, f"Adult {pid}", date(1994, 3, 2), "Attacker")
          for pid in range(100, 106)),
        (11, "Young Prospect", date(2007, 7, 20), "Attacker"),
        (12, "Missing Rating", date(2007, 7, 20), "Attacker"),
        (13, "Small Cohort", date(2006, 7, 20), "Attacker"),
        (14, "Goalkeeper", date(2007, 7, 20), "Goalkeeper"),
        (15, "Unknown DOB", None, "Attacker"),
        (16, "Exactly 22", date(2004, 11, 17), "Attacker"),
        (17, "Future Only", date(2007, 7, 20), "Attacker"),
        (18, "Live Only", date(2007, 7, 20), "Attacker"),
        (19, "Old Season Only", date(2007, 7, 20), "Attacker"),
    ]:
        player = Player(
            provider="api-football", provider_id=str(external_id),
            name=name, birth_date=born, position=pos,
        )
        db.add(player)
        db.flush()
        players[external_id] = player.id

    def appearance(pid, match_id, minutes, rating, goals=0, assists=0,
                   position="Attacker", shots=1, saves=None):
        detail = {
            "games": {"position": position},
            "shots": {"on": shots},
            "passes": {"key": 2},
            "dribbles": {"success": 1},
            "goals": {"saves": saves},
        }
        db.add(PlayerMatchStats(
            match_id=matches[match_id], player_id=players[pid],
            minutes=minutes, rating=rating, goals=goals, assists=assists,
            payload=detail,
        ))

    for pid, score in zip(range(100, 106), (6.0, 6.2, 6.5, 6.7, 6.9, 7.1), strict=True):
        for match_id in (1, 2, 3):
            appearance(pid, match_id, 90, score)
    appearance(11, 1, 90, 9.4, goals=1, assists=1, shots=3)
    appearance(12, 1, 90, None, goals=None, assists=None, shots=None)
    appearance(13, 4, 90, 9.9)
    appearance(14, 1, 90, 9.9, position="Goalkeeper", saves=5)
    appearance(15, 1, 90, 10.0)
    appearance(16, 1, 90, 10.0)
    appearance(17, 6, 90, 10.0)
    appearance(18, 5, 90, 10.0)
    appearance(19, 7, 90, 10.0)
    db.commit()
    return {"leagues": competitions, "players": players}


def test_position_and_metric_parsing() -> None:
    assert position_group("A") == "attacker"
    assert position_group("FW") == "attacker"
    assert position_group("Goalkeeper") == "goalkeeper"
    assert position_group("G") == "goalkeeper"
    assert position_group("Unknown") is None
    assert _number(True) is None
    assert _number("-1") is None
    assert _number("0") == 0


def test_cohort_normalization_low_sample_and_temporal_filters() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        ids = _seed(db)
        result = discover_u22(db, as_of=AS_OF, category="emerging", min_cohort=5)
        assert result["model_type"] == "transparent_heuristic_not_ai"
        assert result["cross_league_ranking"] is False
        assert result["relative_score_is_probability"] is False
        groups = {
            (group["competition_id"], group["position_group"]): group
            for group in result["groups"]
        }
        league1 = groups[(ids["leagues"]["100"], "attacker")]
        assert league1["peer_count"] == 6
        assert league1["cohort_ready"] is True
        young = next(x for x in league1["players"] if x["name"] == "Young Prospect")
        assert young["relative_score"] is not None
        assert 50 < young["relative_score"] < 70  # 90 mins must be shrunk
        assert young["average_rating"] == 9.4
        assert young["confidence"] == 0.1
        assert young["confidence_is_calibrated_probability"] is False
        assert young["features_per90"]["goals_per90"] == 1.0
        assert young["features_per90"]["assists_per90"] == 1.0
        assert young["features_per90"]["shots_on_target_per90"] == 3.0
        assert young["features_per90"]["saves_per90"] is None
        assert "small_playing_sample" in young["signals"]
        assert "early_goal_involvement" in young["signals"]
        assert "rating_above_position_league_peers" in young["signals"]
        assert young["matches"] == 1

        missing = next(x for x in league1["players"] if x["name"] == "Missing Rating")
        assert missing["relative_score"] is None
        assert missing["features_per90"]["goals_per90"] is None
        assert "insufficient_rating_coverage" in missing["signals"]
        assert missing["status"] == "insufficient_rating_data"
        assert "Unknown DOB" not in {x["name"] for x in league1["players"]}
        assert "Exactly 22" not in {x["name"] for x in league1["players"]}
        assert "Future Only" not in {x["name"] for x in league1["players"]}
        assert "Live Only" not in {x["name"] for x in league1["players"]}
        assert "Old Season Only" not in {x["name"] for x in league1["players"]}

        league2 = groups[(ids["leagues"]["200"], "attacker")]
        assert league2["peer_count"] == 0
        assert league2["players"][0]["relative_score"] is None
        assert "insufficient_comparable_peers" in league2["players"][0]["signals"]
        keeper = groups[(ids["leagues"]["100"], "goalkeeper")]
        assert keeper["cohort_ready"] is False
        assert keeper["players"][0]["features_per90"]["saves_per90"] == 5
        assert keeper["players"][0]["relative_score"] is None

        assert discover_u22(
            db, as_of=AS_OF, category="established",
            competition_id=ids["leagues"]["100"],
        )["groups"] == []
        older_asof = discover_u22(
            db, as_of=date(2026, 11, 18), category="emerging",
            competition_id=ids["leagues"]["100"],
        )
        assert all(
            x["name"] != "Exactly 22" for group in older_asof["groups"]
            for x in group["players"]
        )


def test_discovery_and_player_report_routes() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        ids = _seed(db)

    def db_override():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    try:
        client = TestClient(app)
        response = client.get("/api/v1/scouting/u22/discovery", params={
            "as_of": AS_OF.isoformat(), "competition_id": ids["leagues"]["100"],
            "position": "attacker",
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data["groups"]) == 1
        assert data["groups"][0]["peer_count"] == 6
        assert all(x["position_group"] == "attacker" for x in data["groups"][0]["players"])
        report = client.get(
            f"/api/v1/scouting/u22/reports/{ids['players'][11]}",
            params={"as_of": AS_OF.isoformat()},
        )
        assert report.status_code == 200
        assert len(report.json()["reports"]) == 1
        assert report.json()["reports"][0]["name"] == "Young Prospect"
        without_birth = client.get(
            f"/api/v1/scouting/u22/reports/{ids['players'][15]}",
            params={"as_of": AS_OF.isoformat()},
        )
        assert without_birth.status_code == 200
        assert without_birth.json()["reports"] == []
        assert without_birth.json()["date_of_birth_verified"] is False
        assert client.get("/api/v1/scouting/u22/reports/999999").status_code == 404
        assert client.get(
            "/api/v1/scouting/u22/discovery?min_cohort=2"
        ).status_code == 422
        assert client.get(
            "/api/v1/scouting/u22/discovery?position=winger"
        ).status_code == 422
        assert client.get(
            "/api/v1/scouting/u22/discovery?competition_id=99999"
        ).status_code == 404
    finally:
        app.dependency_overrides.clear()
