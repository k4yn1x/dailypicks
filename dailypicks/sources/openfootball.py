"""Adapter for the openfootball/football.json dataset (GitHub, public domain).

Source: https://github.com/openfootball/football.json
Content: fixtures and full-time results per league-season, updated by the
maintainers' auto-update script every few days during the season (the commit
log is recorded in the archive manifest so freshness is auditable).
Kickoff times are in the league's local timezone and are sometimes missing for
future matchdays; we store UTC when a time exists and flag date-only rows.
Limitations: no odds, no match status (postponed/cancelled matches simply move
or lose their date), team names change spelling between seasons (handled by
`teams.canonical_id`).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import ARCHIVE_DIR, COMPETITIONS, DATA_DIR, SCHEMA_VERSION
from ..teams import COUNTRY_OF_COMP, canonical_id, slugify

REPO_URL = "https://github.com/openfootball/football.json.git"
REPO_DIR = DATA_DIR / "openfootball_json"


@dataclass
class RawMatch:
    match_id: str
    competition: str
    season: str
    round: str | None
    date_local: str
    time_local: str | None
    kickoff_utc: str | None
    home_raw: str
    away_raw: str
    home_id: str | None
    away_id: str | None
    home_goals: int | None
    away_goals: int | None
    ht_home: int | None
    ht_away: int | None
    archive_id: str


def sync_repo(offline: bool = False) -> dict:
    """Clone or fast-forward the dataset repo. Returns commit metadata."""
    if not REPO_DIR.exists():
        if offline:
            raise RuntimeError("openfootball repo missing and offline=True")
        subprocess.run(["git", "clone", "-q", "--depth", "1", REPO_URL, str(REPO_DIR)], check=True)
    elif not offline:
        subprocess.run(["git", "-C", str(REPO_DIR), "fetch", "-q", "--depth", "1", "origin", "master"], check=True)
        subprocess.run(["git", "-C", str(REPO_DIR), "reset", "-q", "--hard", "origin/master"], check=True)
    out = subprocess.run(["git", "-C", str(REPO_DIR), "log", "-1", "--format=%H|%cI|%s"],
                         capture_output=True, text=True, check=True).stdout.strip()
    sha, date, msg = out.split("|", 2)
    return {"commit": sha, "commit_date": date, "message": msg}


def _archive_file(path: Path, competition: str, season: str, commit: dict) -> str:
    """Copy the raw input into the archive with provenance and return its id."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    blob = path.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    archive_id = f"openfootball:{season}:{competition}:{sha[:12]}"
    dest = ARCHIVE_DIR / f"{season}_{competition}_{sha[:12]}.json"
    if not dest.exists():
        dest.write_bytes(blob)
        meta = {
            "archive_id": archive_id,
            "source": "openfootball/football.json",
            "url": f"https://raw.githubusercontent.com/openfootball/football.json/{commit['commit']}/{season}/{competition}.json",
            "repo_commit": commit["commit"],
            "repo_commit_date": commit["commit_date"],
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "sha256": sha,
            "competition": competition,
            "season": season,
            "schema_version": SCHEMA_VERSION,
            "real_data": True,
            "bytes": len(blob),
        }
        dest.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2))
    return archive_id


def _ft(m: dict) -> tuple[int | None, int | None, int | None, int | None]:
    s = m.get("score")
    if isinstance(s, dict):
        ft = s.get("ft")
        ht = s.get("ht")
        hg, ag = (ft[0], ft[1]) if ft else (None, None)
        h1, a1 = (ht[0], ht[1]) if ht else (None, None)
        return hg, ag, h1, a1
    if isinstance(s, list) and len(s) == 2:
        return s[0], s[1], None, None
    if "score1" in m and m.get("score1") is not None:
        return m["score1"], m["score2"], m.get("score1i"), m.get("score2i")
    return None, None, None, None


_NON_LEAGUE = re.compile(r"playoff|play-off|final|semifinal|relegation|aufstieg|europe|championship round", re.I)


def is_regular_season(m: dict) -> bool:
    stage = (m.get("stage") or "").lower()
    if stage and stage not in {"regular", "regular season"}:
        return False
    rnd = m.get("round") or ""
    return not _NON_LEAGUE.search(rnd)


def load_league_season(competition: str, season: str, commit: dict) -> list[RawMatch] | None:
    path = REPO_DIR / season / f"{competition}.json"
    if not path.exists():
        return None
    archive_id = _archive_file(path, competition, season, commit)
    data = json.loads(path.read_text())
    tz = ZoneInfo(COMPETITIONS[competition]["tz"])
    country = COUNTRY_OF_COMP[competition]
    rows: list[RawMatch] = []
    for m in data["matches"]:
        if not is_regular_season(m):
            continue  # play-offs / finals are not league matches; excluded from model and board
        date = m["date"]
        time = m.get("time")
        kickoff_utc = None
        if time:
            local = datetime.fromisoformat(f"{date}T{time}").replace(tzinfo=tz)
            kickoff_utc = local.astimezone(timezone.utc).isoformat()
        hid = canonical_id(country, m["team1"])
        aid = canonical_id(country, m["team2"])
        hg, ag, h1, a1 = _ft(m)
        mid = f"{competition}:{season}:{date}:{slugify(m['team1'])}:{slugify(m['team2'])}"
        rows.append(RawMatch(mid, competition, season, m.get("round"), date, time, kickoff_utc,
                             m["team1"], m["team2"], hid, aid, hg, ag, h1, a1, archive_id))
    return rows


def rows_as_dicts(rows: list[RawMatch]) -> list[dict]:
    return [asdict(r) for r in rows]
