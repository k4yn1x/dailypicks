"""Ingestion: sync sources -> archive -> validate -> atomic dataset publish."""
from __future__ import annotations

import json
from pathlib import Path

from .config import ACTIVE_COMPETITIONS, ARCHIVE_DIR, SEASONS
from .sources import footballdata, openfootball
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
    # closing odds (evaluation only) from football-data.co.uk extracts, if present
    odds_rows, odds_cov = [], {}
    fd_rows = footballdata.load_rows()
    if fd_rows:
        index = {}
        score = {}
        for rows in all_rows.values():
            for r in rows:
                index[(r["competition"], r["date_local"], r["home_id"], r["away_id"])] = r["match_id"]
                score[r["match_id"]] = (r["home_goals"], r["away_goals"])
        odds_rows, odds_cov = footballdata.odds_snapshot_rows(fd_rows, index)
        # independent result cross-check: football-data scores vs primary source
        mism = {}
        for r in fd_rows:
            mid = index.get((r["competition"], r["date_local"], r["home_id"], r["away_id"]))
            if mid and r["fthg"] is not None and score.get(mid) != (r["fthg"], r["ftag"]) and score.get(mid) != (None, None):
                mism.setdefault(f"{r['competition']}:{r['season']}", []).append(f"{mid} fd {r['fthg']}-{r['ftag']} vs {score[mid]}")
        for k, v in mism.items():
            odds_cov.setdefault(k, {})["score_mismatch"] = len(v)
            odds_cov[k]["score_mismatch_examples"] = v[:5]
        meta["footballdata_seasons"] = sorted(odds_cov)
    path = build_dataset(all_rows, archives, meta, odds_rows=odds_rows, odds_coverage=odds_cov)
    init_ledger()
    return {"dataset": str(path), "league_seasons": len(all_rows), "missing": missing, "commit": commit}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
