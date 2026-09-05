"""Ingestion: sync sources -> archive -> validate -> atomic dataset publish."""
from __future__ import annotations

import json
from pathlib import Path

from .config import ACTIVE_COMPETITIONS, ARCHIVE_DIR, SEASONS
from .sources import openfootball
from .store import build_dataset, init_ledger, utcnow, iso


def run(offline: bool = False, competitions: list[str] | None = None, seasons: list[str] | None = None) -> dict:
    competitions = competitions or ACTIVE_COMPETITIONS
    seasons = seasons or SEASONS
    commit = openfootball.sync_repo(offline=offline)
    all_rows: dict[tuple[str, str], list[dict]] = {}
    archives: list[dict] = []
    missing = []
    for season in seasons:
        for comp in competitions:
            rows = openfootball.load_league_season(comp, season, commit)
            if rows is None:
                missing.append((comp, season))
                continue
            all_rows[(comp, season)] = openfootball.rows_as_dicts(rows)
            meta_path = next(ARCHIVE_DIR.glob(f"{season}_{comp}_*.meta.json"))
            for p in ARCHIVE_DIR.glob(f"{season}_{comp}_*.meta.json"):
                meta = json.loads(p.read_text())
                if meta["archive_id"] == rows[0].archive_id:
                    archives.append(meta)
    meta = {"data_commit": commit, "ingested_at": iso(utcnow()), "missing_league_seasons": missing,
            "competitions": competitions}
    path = build_dataset(all_rows, archives, meta)
    init_ledger()
    return {"dataset": str(path), "league_seasons": len(all_rows), "missing": missing, "commit": commit}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
