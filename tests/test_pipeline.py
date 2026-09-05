import json
from datetime import datetime, timedelta, timezone

import pytest

from dailypicks import pipeline, store
from dailypicks.config import POLICY
from tests.conftest import ARCHIVE, make_rows


@pytest.fixture
def env(tmp_store, monkeypatch):
    monkeypatch.setattr(pipeline, "LATEST_PATH", tmp_store / "published" / "latest.json")
    monkeypatch.setattr(pipeline, "STATUS_PATH", tmp_store / "published" / "status.json")
    monkeypatch.setattr(pipeline, "BOARDS_DIR", tmp_store / "published" / "boards")
    monkeypatch.setattr(pipeline, "LOCK_PATH", tmp_store / "run.lock")
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_store)
    monkeypatch.setattr(pipeline, "LEDGER_PATH", tmp_store / "ledger.sqlite")
    monkeypatch.setattr(pipeline, "DATASET_PATH", tmp_store / "dataset.sqlite")
    return tmp_store


def test_kickoff_exclusion_and_timezone_grouping(env):
    now = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
    m = {"match_id": "x", "competition": "en.1", "season": "2025-26", "home_id": "en:a", "away_id": "en:b", "home_raw": "A", "away_raw": "B",
         "kickoff_utc": (now + timedelta(minutes=10)).isoformat(), "date_local": "2026-01-10", "has_result": 0}
    rec = pipeline.rate_fixture(None, m, now, False)
    assert not rec["rated"] and "buffer" in rec["reason"]
    m["kickoff_utc"] = now.isoformat(); m["has_result"] = 1
    assert "completed" in pipeline.rate_fixture(None, m, now, False)["reason"]
    m["kickoff_utc"] = None; m["has_result"] = 0
    assert "kickoff time" in pipeline.rate_fixture(None, m, now, False)["reason"]
    # 02:00 UTC on Jan 11 is still Jan 10 in Chicago -> grouped with the Jan 10 board
    d, t = pipeline._display_dt("2026-01-11T02:00:00+00:00", "America/Chicago")
    assert d == "2026-01-10" and t == "20:00"


def test_failed_run_preserves_previous_board_and_flags_status(env, monkeypatch):
    pipeline.LATEST_PATH.parent.mkdir(parents=True)
    pipeline.LATEST_PATH.write_text(json.dumps({"days": [], "generated_at_utc": "old"}))
    pipeline.atomic_write_json(pipeline.STATUS_PATH, {"last_success_utc": "old-time", "status": "published"})

    def boom(**kw):
        raise RuntimeError("source unreachable")
    monkeypatch.setattr(pipeline.ingest, "run", boom)
    out = pipeline.run_daily(now=datetime(2026, 1, 10, 10, 30, tzinfo=timezone.utc), trigger="test")
    assert out["status"] == "failed" and "source unreachable" in out["error"]
    assert json.loads(pipeline.LATEST_PATH.read_text())["generated_at_utc"] == "old"   # untouched
    st = json.loads(pipeline.STATUS_PATH.read_text())
    assert st["status"] == "failed" and st["last_success_utc"] == "old-time" and st["error"]
    with store.connect(pipeline.LEDGER_PATH, readonly=True) as con:
        assert con.execute("SELECT status FROM runs").fetchone()[0] == "failed"


def test_validate_published_rejects_over_cap_and_bad_probabilities():
    day = {"n_picks": 1, "fixtures": [{"match_id": "a", "status": "pick", "sim": {"valid": 10000},
            "primary": {"survival": 0.81, "p_win": 0.81, "p_push": 0.0, "p_loss": 0.19}, "markets": []}]}
    pipeline.validate_published({"days": [day]})
    day["fixtures"][0]["primary"]["survival"] = 0.79
    with pytest.raises(AssertionError):
        pipeline.validate_published({"days": [day]})
    day["fixtures"][0]["primary"]["survival"] = 0.81; day["fixtures"][0]["sim"]["valid"] = 9999
    with pytest.raises(AssertionError):
        pipeline.validate_published({"days": [day]})
    many = {"n_picks": POLICY["max_picks"] + 1, "fixtures": [dict(day["fixtures"][0], match_id=str(i)) for i in range(POLICY["max_picks"] + 1)]}
    many["fixtures"][0]["sim"]["valid"] = 10000
    with pytest.raises(AssertionError):
        pipeline.validate_published({"days": [many]})
