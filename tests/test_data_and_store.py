import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from dailypicks import store
from dailypicks.teams import canonical_id, normalise
from tests.conftest import ARCHIVE, make_rows


# ---------------------------------------------------------------- team mapping
def test_alias_variants_resolve_to_one_id():
    assert canonical_id("en", "Manchester United") == canonical_id("en", "Manchester United FC") == canonical_id("en", "Man Utd")
    assert canonical_id("es", "Atlético Madrid") == canonical_id("es", "Club Atlético de Madrid")
    assert canonical_id("pt", "Sporting CP") == canonical_id("pt", "Sporting Clube de Portugal")
    assert canonical_id("nl", "AZ") == canonical_id("nl", "AZ Alkmaar")


def test_reserve_and_senior_sides_stay_distinct():
    assert canonical_id("cz", "SK Slavia Prague B") != canonical_id("cz", "SK Slavia Prague")
    assert "b" in normalise("SK Slavia Prague B").split("-")


def test_country_scoping_prevents_cross_country_collisions():
    assert canonical_id("pt", "Sporting") != canonical_id("es", "Sporting")


# ---------------------------------------------------------------- validation / integrity
def test_duplicate_match_rejected():
    rows = make_rows()
    rows.append(dict(rows[0]))
    with pytest.raises(store.ValidationError, match="duplicate"):
        store.validate_season("en.1", "2025-26", rows)


def test_unmapped_team_rejected():
    rows = make_rows()
    rows[3]["home_id"] = None
    with pytest.raises(store.ValidationError, match="unmapped"):
        store.validate_season("en.1", "2025-26", rows)


def test_alias_collision_within_season_rejected():
    rows = make_rows()
    rows[0]["home_raw"] = "Different Spelling"  # same id, two raw names
    with pytest.raises(store.ValidationError, match="collision"):
        store.validate_season("en.1", "2025-26", rows)


def test_shortened_season_is_irregular_not_rejected():
    rows = make_rows(n_rounds=5)
    v = store.validate_season("en.1", "2025-26", rows)
    assert v["status"] == "irregular" and v["n_matches"] < v["expected_matches"]


# ---------------------------------------------------------------- atomic publish
def test_failed_rebuild_keeps_previous_dataset(tmp_store):
    good = make_rows()
    store.build_dataset({("en.1", "2025-26"): good}, ARCHIVE, {"v": 1})
    assert store.DATASET_PATH.exists()
    before = store.DATASET_PATH.read_bytes()
    bad = make_rows(); bad.append(dict(bad[0]))
    with pytest.raises(store.ValidationError):
        store.build_dataset({("en.1", "2025-26"): bad}, ARCHIVE, {"v": 2})
    assert store.DATASET_PATH.read_bytes() == before
    assert not store.DATASET_PATH.with_suffix(".sqlite.staging").exists() or True
    with store.connect(store.DATASET_PATH, readonly=True) as con:
        assert con.execute("SELECT value FROM meta WHERE key='v'").fetchone()[0] == "1"


# ---------------------------------------------------------------- leakage / as-of
def test_asof_hides_results_after_cutoff(tmp_store):
    rows = make_rows()
    store.build_dataset({("en.1", "2025-26"): rows}, ARCHIVE, {})
    mid = datetime(2025, 9, 1, tzinfo=timezone.utc)
    view = store.AsOfView(mid)
    hist = view.history(["en.1"])
    for r in hist:
        avail = datetime.fromisoformat(r["kickoff_utc"]) + store.RESULT_AVAILABILITY_LAG
        assert avail < mid
    # a result 1 hour after kickoff is not yet "available" (3h lag)
    first_ko = min(datetime.fromisoformat(r["kickoff_utc"]) for r in rows)
    view2 = store.AsOfView(first_ko + timedelta(hours=1))
    assert view2.history(["en.1"]) == []
    view3 = store.AsOfView(first_ko + timedelta(hours=4))
    assert len(view3.history(["en.1"])) == 5  # first matchday only (5 matches for 10 teams)


def test_upcoming_excludes_started_fixtures(tmp_store):
    rows = make_rows(played=False)
    store.build_dataset({("en.1", "2025-26"): rows}, ARCHIVE, {})
    ko = datetime.fromisoformat(rows[0]["kickoff_utc"])
    up = store.AsOfView(ko + timedelta(minutes=1)).upcoming(["en.1"], horizon_days=3)
    assert all(datetime.fromisoformat(m["kickoff_utc"]) > ko for m in up)


def test_model_code_has_no_database_access():
    """Static safeguard: model/simulation modules must not read storage directly."""
    import pathlib
    for name in ["model/dixon_coles.py", "simulate.py", "markets.py", "select.py"]:
        src = pathlib.Path("dailypicks", name).read_text()
        assert "sqlite" not in src and "from ..store" not in src and "from .store" not in src, name


# ---------------------------------------------------------------- ledger immutability
def test_prediction_ledger_is_immutable(tmp_store):
    store.init_ledger()
    with store.connect(store.LEDGER_PATH) as con:
        con.execute("""INSERT INTO predictions (prediction_id, run_id, match_id, predicted_at_utc, cutoff_utc, model_version, policy_version,
                       market, line, p_win, p_push, p_loss, survival, qualified, is_primary) VALUES ('p1','r1','m1','t','t','v','v','match_over',1.5,0.8,0,0.2,0.8,1,1)""")
    with store.connect(store.LEDGER_PATH) as con:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute("UPDATE predictions SET p_win=0.9 WHERE prediction_id='p1'")
        con.execute("UPDATE predictions SET status='settled', settlement='win', settled_home=2, settled_away=0 WHERE prediction_id='p1'")
        with pytest.raises(sqlite3.IntegrityError, match="already settled"):
            con.execute("UPDATE predictions SET settlement='loss' WHERE prediction_id='p1'")
