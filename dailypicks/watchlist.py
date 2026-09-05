"""Ticket-team watchlist: every team visible in the user's ticket screenshots,
deduplicated, with its coverage status against the ingested dataset.

Statuses:
  supported         — mapped to a canonical team that plays in a supported competition this season
  historical_only   — mapped, but not in a supported competition this season (e.g. relegated / cup tie)
  awaiting_coverage — the team's league is not ingested (no source yet)
  ambiguous         — could not be mapped without guessing (needs review)
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from .config import ACTIVE_COMPETITIONS, COMPETITIONS, CURRENT_SEASON, DATA_DIR
from .store import DATASET_PATH, connect
from .teams import COUNTRY_OF_COMP, canonical_id, slugify

TICKETS_PATH = DATA_DIR / "watchlist" / "ticket_lines.json"
WATCHLIST_PATH = DATA_DIR / "watchlist" / "watchlist.json"

COUNTRY_NAMES = {"en": "England", "de": "Germany", "es": "Spain", "it": "Italy", "fr": "France", "nl": "Netherlands",
                 "pt": "Portugal", "rs": "Serbia", "tr": "Türkiye", "hr": "Croatia", "cz": "Czechia", "cy": "Cyprus",
                 "se": "Sweden", "dk": "Denmark", "fo": "Faroe Islands", "pl": "Poland", "ae": "UAE", "sa": "Saudi Arabia",
                 "be": "Belgium", "hu": "Hungary", "sco": "Scotland", "qa": "Qatar", "at": "Austria", "no": "Norway",
                 "ch": "Switzerland", "si": "Slovenia", "wal": "Wales"}
SUPPORTED_COUNTRIES = {COUNTRY_OF_COMP[c] for c in ACTIVE_COMPETITIONS}


def _teams_in_dataset() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(current-season team ids by competition, all-time team ids by competition)."""
    cur, hist = {}, {}
    with connect(DATASET_PATH, readonly=True) as con:
        for comp in ACTIVE_COMPETITIONS:
            cur[comp] = {r[0] for r in con.execute("SELECT DISTINCT home_id FROM matches WHERE competition=? AND season=?", (comp, CURRENT_SEASON))}
            hist[comp] = {r[0] for r in con.execute("SELECT DISTINCT home_id FROM matches WHERE competition=?", (comp,))}
    return cur, hist


def build() -> dict:
    lines = json.loads(TICKETS_PATH.read_text())["lines"]
    cur, hist = _teams_in_dataset()
    entries: "OrderedDict[str, dict]" = OrderedDict()
    for ln in lines:
        hints = ln["country_hint"].split("/")
        for side, name in (("home", ln["home"]), ("away", ln["away"])):
            country = hints[0] if side == "home" or len(hints) == 1 else hints[1]
            key = f"{country}:{slugify(name)}"
            e = entries.setdefault(key, {"ticket_name": name, "country": country, "country_name": COUNTRY_NAMES.get(country, country),
                                         "appearances": 0, "tickets": set(), "reserve_or_youth": False})
            e["appearances"] += 1
            e["tickets"].add(ln["ticket"])
            if name.endswith(" B") or " U19" in name or " U21" in name or "Jong " in name:
                e["reserve_or_youth"] = True
    out = []
    for key, e in entries.items():
        country = e["country"]
        rec = {**e, "tickets": sorted(e["tickets"]), "canonical_id": None, "competition": None, "status": None, "note": ""}
        if country not in SUPPORTED_COUNTRIES:
            rec["status"] = "awaiting_coverage"; rec["note"] = f"{rec['country_name']}: no supported competition ingested yet"
        else:
            cid = canonical_id(country, e["ticket_name"])
            comps_cur = [c for c in ACTIVE_COMPETITIONS if COUNTRY_OF_COMP[c] == country and cid in cur[c]]
            comps_hist = [c for c in ACTIVE_COMPETITIONS if COUNTRY_OF_COMP[c] == country and cid in hist[c]]
            if e["reserve_or_youth"]:
                rec["status"] = "awaiting_coverage"; rec["canonical_id"] = cid
                rec["note"] = "reserve/youth side — kept distinct from the senior team; no reserve-league source"
            elif comps_cur:
                rec["status"] = "supported"; rec["canonical_id"] = cid; rec["competition"] = comps_cur[0]
            elif comps_hist:
                rec["status"] = "historical_only"; rec["canonical_id"] = cid; rec["competition"] = comps_hist[0]
                rec["note"] = f"seen in {COMPETITIONS[comps_hist[0]]['name']} in past seasons, not this season (cup tie or lower division)"
            else:
                rec["status"] = "awaiting_coverage"; rec["canonical_id"] = cid
                rec["note"] = f"{rec['country_name']}: plays outside the ingested competitions (cup / lower division)"
        out.append(rec)
    summary = {s: sum(1 for r in out if r["status"] == s) for s in ("supported", "historical_only", "awaiting_coverage", "ambiguous")}
    result = {"n_ticket_lines": len(lines), "n_teams": len(out), "summary": summary, "teams": out}
    WATCHLIST_PATH.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return result


def supported_ids() -> set[str]:
    if not WATCHLIST_PATH.exists():
        build()
    d = json.loads(WATCHLIST_PATH.read_text())
    return {t["canonical_id"] for t in d["teams"] if t["status"] == "supported"}


if __name__ == "__main__":
    r = build()
    print(json.dumps(r["summary"]))
    for t in r["teams"]:
        print(f"{t['status']:18} {t['country']:4} {t['ticket_name']:32} -> {t['canonical_id'] or '-':35} {t['note']}")
