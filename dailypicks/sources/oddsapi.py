"""Current bookmaker odds for match totals and team totals (The Odds API v4).

Requires ODDS_API_KEY in the environment (free tier: 500 requests/month). When
the key is absent or the host is unreachable, `fetch_fixture_odds` returns an
empty mapping and the pipeline records `odds_source.status` accordingly — the
board then shows model break-even prices clearly labelled as estimates and
never an invented bookmaker price.

Every price is stored with bookmaker, market, line, side, decimal price and
the bookmaker's `last_update` timestamp (plus our retrieval time).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import requests

from ..teams import COUNTRY_OF_COMP, canonical_id

SPORT_KEY = {"en.1": "soccer_epl", "de.1": "soccer_germany_bundesliga", "es.1": "soccer_spain_la_liga", "it.1": "soccer_italy_serie_a",
             "fr.1": "soccer_france_ligue_one", "nl.1": "soccer_netherlands_eredivisie", "pt.1": "soccer_portugal_primeira_liga",
             "en.2": "soccer_efl_champ"}
BASE = "https://api.the-odds-api.com/v4"
MARKET_KEYS = "totals,alternate_totals,team_totals,alternate_team_totals"


def configured() -> bool:
    return bool(os.environ.get("ODDS_API_KEY"))


def fetch_fixture_odds(competitions: list[str], timeout: int = 20) -> tuple[dict, dict]:
    """Returns ({(competition, home_id, away_id): {(market, line): [price rows]}}, status)."""
    key = os.environ.get("ODDS_API_KEY")
    status = {"source": "the-odds-api.com", "status": "not configured", "retrieved_at": None, "events": 0, "detail": ""}
    if not key:
        return {}, status
    out: dict = {}
    errors = []
    retrieved = datetime.now(timezone.utc).isoformat()
    for comp in competitions:
        sport = SPORT_KEY.get(comp)
        if not sport:
            continue
        try:
            r = requests.get(f"{BASE}/sports/{sport}/events", params={"apiKey": key}, timeout=timeout)
            r.raise_for_status()
            events = r.json()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{comp}: {type(e).__name__}"); continue
        country = COUNTRY_OF_COMP[comp]
        for ev in events:
            hid, aid = canonical_id(country, ev["home_team"]), canonical_id(country, ev["away_team"])
            try:
                r = requests.get(f"{BASE}/sports/{sport}/events/{ev['id']}/odds",
                                 params={"apiKey": key, "regions": "eu,uk", "markets": MARKET_KEYS, "oddsFormat": "decimal"}, timeout=timeout)
                r.raise_for_status()
                data = r.json()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{comp}/{ev['id']}: {type(e).__name__}"); continue
            rows: dict = {}
            for bk in data.get("bookmakers", []):
                for mk in bk.get("markets", []):
                    for oc in mk.get("outcomes", []):
                        if oc.get("name") != "Over" or oc.get("point") is None:
                            continue
                        if mk["key"] in ("totals", "alternate_totals"):
                            market = "match_over"
                        else:
                            team = canonical_id(country, oc.get("description", ""))
                            market = "home_over" if team == hid else "away_over" if team == aid else None
                        if market is None:
                            continue
                        rows.setdefault((market, float(oc["point"])), []).append(
                            {"bookmaker": bk["title"], "price": float(oc["price"]), "last_update": mk.get("last_update") or bk.get("last_update"),
                             "retrieved_at": retrieved, "market_key": mk["key"]})
            if rows:
                out[(comp, hid, aid)] = rows
                status["events"] += 1
    status["status"] = "ok" if out and not errors else ("partial" if out else "unavailable")
    status["retrieved_at"] = retrieved
    status["detail"] = "; ".join(sorted(set(errors)))[:300]
    return out, status


def best_price(rows: list[dict]) -> dict | None:
    """Highest available decimal price for a line, with its bookmaker and timestamp."""
    if not rows:
        return None
    return max(rows, key=lambda r: r["price"])
