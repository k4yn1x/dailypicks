import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dailypicks import config, store


def make_rows(comp="en.1", season="2025-26", teams=None, start=datetime(2025, 8, 9, 14, tzinfo=timezone.utc),
              seed=1, played=True, n_rounds=None):
    """Synthetic double round-robin with Poisson-ish scores (real-looking structure, clearly synthetic)."""
    teams = teams or [f"en:team-{i:02d}" for i in range(10)]
    rng = random.Random(seed)
    rows, archive = [], "synthetic:test"
    pairs = [(h, a) for h in teams for a in teams if h != a]
    rng.shuffle(pairs)
    per_round = len(teams) // 2
    for k, (h, a) in enumerate(pairs):
        rnd = k // per_round
        if n_rounds is not None and rnd >= n_rounds:
            break
        ko = start + timedelta(days=7 * rnd)
        hg = ag = None
        if played:
            hg = min(9, int(rng.expovariate(1 / 1.5))); ag = min(9, int(rng.expovariate(1 / 1.1)))
        rows.append({"match_id": f"{comp}:{season}:{ko.date()}:{h}:{a}", "competition": comp, "season": season, "round": f"Matchday {rnd + 1}",
                     "date_local": ko.date().isoformat(), "time_local": "15:00", "kickoff_utc": ko.isoformat(),
                     "home_raw": h.split(":")[1].title(), "away_raw": a.split(":")[1].title(), "home_id": h, "away_id": a,
                     "home_goals": hg, "away_goals": ag, "ht_home": None, "ht_away": None, "archive_id": archive})
    return rows


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATASET_PATH", tmp_path / "dataset.sqlite")
    monkeypatch.setattr(store, "LEDGER_PATH", tmp_path / "ledger.sqlite")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    return tmp_path


ARCHIVE = [{"archive_id": "synthetic:test", "source": "synthetic", "url": "", "retrieved_at": "2025-01-01T00:00:00+00:00",
            "sha256": "0", "competition": "en.1", "season": "2025-26", "schema_version": "1", "real_data": False}]
