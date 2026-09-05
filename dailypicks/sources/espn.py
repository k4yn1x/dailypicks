"""Independent fixture cross-check against ESPN's public scoreboard endpoint.

    https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates=YYYYMMDD

Used only to VERIFY fixtures (teams, kickoff, status) produced from the primary
source; never as training data. If the endpoint is unreachable from the
execution environment, the board is published with `cross_check = unavailable`
and every fixture is labelled "unverified" — fixtures are not invented from it.
A pre-fetched copy can be dropped in data/espn/<YYYYMMDD>_<league>.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..config import DATA_DIR
from ..teams import COUNTRY_OF_COMP, canonical_id

ESPN_LEAGUE = {"en.1": "eng.1", "de.1": "ger.1", "es.1": "esp.1", "it.1": "ita.1", "fr.1": "fra.1",
               "nl.1": "ned.1", "pt.1": "por.1", "en.2": "eng.2"}
CACHE_DIR = DATA_DIR / "espn"
URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={date}"

# ESPN spellings that the generic normaliser does not resolve.
_EXTRA = {
    ("en", "spurs"): "tottenham-hotspur", ("de", "bayern-munich"): "bayern-munchen",
    ("it", "internazionale"): "internazionale", ("it", "inter-milan"): "internazionale",
    ("nl", "ajax-amsterdam"): "ajax", ("nl", "nec-nijmegen"): "nec", ("pt", "vitoria-guimaraes"): "vitoria-guimaraes",
    ("pt", "sporting-cp"): "sporting-portugal", ("pt", "sporting-lisbon"): "sporting-portugal",
    ("es", "atletico-madrid"): "atletico-madrid", ("fr", "paris-saint-germain"): "paris-saint-germain",
    ("pt", "estrela"): "estrela-amadora", ("pt", "c.d.-nacional"): "nacional", ("pt", "vitoria-guimaraes"): "vitoria-guimaraes",
    ("de", "hamburg-sv"): "hamburger", ("de", "fc-cologne"): "koln", ("de", "cologne"): "koln", ("es", "deportivo"): "deportivo-la-coruna",
}


def fetch(competition: str, date: str, timeout: int = 20) -> dict:
    """Fetch and cache the ESPN scoreboard for a competition on `date` (YYYY-MM-DD)."""
    d = date.replace("-", "")
    league = ESPN_LEAGUE[competition]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    r = requests.get(URL.format(league=league, date=d), timeout=timeout, headers={"User-Agent": "dailypicks/1.0"})
    r.raise_for_status()
    data = r.json()
    (CACHE_DIR / f"{d}_{competition}.json").write_text(json.dumps({"retrieved_at": datetime.now(timezone.utc).isoformat(), "data": data}))
    return data


def load_cached(competition: str, date: str, max_age_hours: float = 36) -> dict | None:
    p = CACHE_DIR / f"{date.replace('-', '')}_{competition}.json"
    if not p.exists():
        return None
    blob = json.loads(p.read_text())
    age = datetime.now(timezone.utc) - datetime.fromisoformat(blob["retrieved_at"])
    if age > timedelta(hours=max_age_hours):
        return None
    return blob["data"]


def events(competition: str, data: dict) -> list[dict]:
    country = COUNTRY_OF_COMP[competition]
    out = []
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        home = away = None
        for c in comp["competitors"]:
            if c["homeAway"] == "home":
                home = c["team"]
            else:
                away = c["team"]
        status = comp.get("status", {}).get("type", {})
        out.append({
            "home_raw": home["displayName"], "away_raw": away["displayName"],
            "home_id": _cid(country, home["displayName"]), "away_id": _cid(country, away["displayName"]),
            "kickoff_utc": ev["date"].replace("Z", "+00:00") if ev.get("date") else None,
            "state": status.get("state"), "detail": status.get("description"), "completed": status.get("completed"),
        })
    return out


def _cid(country: str, name: str) -> str | None:
    from ..teams import normalise
    n = normalise(name)
    slug = _EXTRA.get((country, n))
    return f"{country}:{slug}" if slug else canonical_id(country, name)


def cross_check(fixture: dict, evs: list[dict], tolerance_minutes: int = 20) -> dict:
    """Compare one primary-source fixture with ESPN events for the same date."""
    for e in evs:
        if e["home_id"] == fixture["home_id"] and e["away_id"] == fixture["away_id"]:
            res = {"verified": True, "espn_state": e["state"], "espn_detail": e["detail"], "kickoff_match": None}
            if fixture.get("kickoff_utc") and e["kickoff_utc"]:
                dt = abs(datetime.fromisoformat(fixture["kickoff_utc"]) - datetime.fromisoformat(e["kickoff_utc"]))
                res["kickoff_match"] = dt <= timedelta(minutes=tolerance_minutes)
                res["espn_kickoff_utc"] = e["kickoff_utc"]
            else:
                res["espn_kickoff_utc"] = e["kickoff_utc"]
            return res
    return {"verified": False, "espn_state": None, "espn_detail": "fixture not found on ESPN for this date"}


def find_on_other_dates(fixture: dict, competition: str, date: str, window_days: int = 3) -> str | None:
    """If the pairing exists on a neighbouring date in the cache, return that ESPN kickoff (helps explain mismatches)."""
    base = datetime.fromisoformat(date)
    for delta in range(-window_days, window_days + 1):
        if delta == 0:
            continue
        d = (base + timedelta(days=delta)).date().isoformat()
        data = load_cached(competition, d, max_age_hours=1e9)
        if not data:
            continue
        for e in events(competition, data):
            if e["home_id"] == fixture["home_id"] and e["away_id"] == fixture["away_id"]:
                return e["kickoff_utc"]
    return None
