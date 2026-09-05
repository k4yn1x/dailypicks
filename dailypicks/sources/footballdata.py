"""football-data.co.uk closing odds (Over/Under 2.5) for evaluation baselines.

Source: https://www.football-data.co.uk/mmz4281/<season>/<div>.csv (free, non-commercial use).
Columns used: PC>2.5 / PC<2.5 (Pinnacle closing), MaxC / AvgC (market max / average closing).
The site is not reachable from the cloud execution environment, so compact
extracts are produced in the user's browser (see scripts/parse_browser_result.py)
and kept in data/fdcouk/batch*.json; each row is
  "DD/MM/YYYY|Home|Away|FTHG|FTAG|PC>|PC<|MaxC>|MaxC<|AvgC>|AvgC<".
Closing odds are only ever used for EVALUATION (kind='closing'); they are never
visible to model fitting (the as-of view exposes results only).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR
from ..teams import COUNTRY_OF_COMP, canonical_id

FD_DIR = DATA_DIR / "fdcouk"
DIV_TO_COMP = {"E0": "en.1", "D1": "de.1", "SP1": "es.1", "I1": "it.1", "F1": "fr.1", "N1": "nl.1", "P1": "pt.1", "E1": "en.2"}

# football-data spellings -> canonical slug (country scoped)
FD_ALIASES = {
    ("en", "man-united"): "manchester-united", ("en", "man-city"): "manchester-city", ("en", "nottm-forest"): "nottingham-forest",
    ("en", "sheffield-united"): "sheffield-united", ("en", "sheffield-weds"): "sheffield-wednesday", ("en", "qpr"): "queens-park-rangers",
    ("en", "west-brom"): "west-bromwich-albion", ("en", "wolves"): "wolverhampton-wanderers", ("en", "brighton"): "brighton-and-hove-albion",
    ("en", "tottenham"): "tottenham-hotspur", ("en", "newcastle"): "newcastle-united", ("en", "leeds"): "leeds-united", ("en", "ipswich"): "ipswich-town",
    ("en", "luton"): "luton-town", ("en", "hull"): "hull-city", ("en", "leicester"): "leicester-city", ("en", "southampton"): "southampton",
    ("en", "west-ham"): "west-ham-united", ("en", "cardiff"): "cardiff-city", ("en", "swansea"): "swansea-city", ("en", "stoke"): "stoke-city",
    ("en", "norwich"): "norwich-city", ("en", "derby"): "derby-county", ("en", "preston"): "preston-north-end", ("en", "blackburn"): "blackburn-rovers",
    ("en", "bristol-city"): "bristol-city", ("en", "coventry"): "coventry-city", ("en", "plymouth"): "plymouth-argyle", ("en", "oxford"): "oxford-united",
    ("en", "portsmouth"): "portsmouth", ("en", "middlesbrough"): "middlesbrough", ("en", "millwall"): "millwall", ("en", "watford"): "watford",
    ("en", "burnley"): "burnley", ("en", "sunderland"): "sunderland", ("en", "charlton"): "charlton-athletic", ("en", "wrexham"): "wrexham",
    ("en", "birmingham"): "birmingham-city", ("en", "bolton"): "bolton-wanderers", ("en", "lincoln"): "lincoln-city",
    ("de", "m-gladbach"): "borussia-monchengladbach", ("de", "mgladbach"): "borussia-monchengladbach", ("de", "leverkusen"): "bayer-leverkusen",
    ("de", "bayern-munich"): "bayern-munchen", ("de", "dortmund"): "borussia-dortmund", ("de", "ein-frankfurt"): "eintracht-frankfurt",
    ("de", "fc-koln"): "koln", ("de", "hamburg"): "hamburger", ("de", "st-pauli"): "st.-pauli", ("de", "union-berlin"): "union-berlin",
    ("de", "heidenheim"): "heidenheim", ("de", "hoffenheim"): "hoffenheim", ("de", "kiel"): "holstein-kiel", ("de", "holstein-kiel"): "holstein-kiel",
    ("de", "bochum"): "bochum", ("de", "werder-bremen"): "werder-bremen", ("de", "stuttgart"): "stuttgart", ("de", "wolfsburg"): "wolfsburg",
    ("de", "rb-leipzig"): "rb-leipzig", ("de", "mainz"): "mainz", ("de", "freiburg"): "freiburg", ("de", "augsburg"): "augsburg",
    ("es", "ath-bilbao"): "athletic", ("es", "ath-madrid"): "atletico-madrid", ("es", "vallecano"): "rayo-vallecano", ("es", "sociedad"): "real-sociedad",
    ("es", "betis"): "real-betis", ("es", "celta"): "celta-vigo", ("es", "espanol"): "espanyol", ("es", "la-coruna"): "deportivo-la-coruna",
    ("es", "valladolid"): "valladolid", ("es", "alaves"): "alaves", ("es", "las-palmas"): "las-palmas", ("es", "leganes"): "leganes",
    ("es", "girona"): "girona", ("es", "mallorca"): "mallorca", ("es", "oviedo"): "real-oviedo",
    ("it", "inter"): "internazionale", ("it", "milan"): "milan", ("it", "verona"): "hellas-verona", ("it", "roma"): "roma", ("it", "lazio"): "lazio",
    ("fr", "paris-sg"): "paris-saint-germain", ("fr", "st-etienne"): "saint-etienne", ("fr", "lyon"): "lyon", ("fr", "marseille"): "marseille",
    ("fr", "monaco"): "monaco", ("fr", "lens"): "lens", ("fr", "lille"): "lille", ("fr", "nice"): "nice", ("fr", "rennes"): "rennes",
    ("fr", "brest"): "brest", ("fr", "reims"): "reims", ("fr", "toulouse"): "toulouse", ("fr", "nantes"): "nantes", ("fr", "montpellier"): "montpellier",
    ("fr", "strasbourg"): "strasbourg", ("fr", "le-havre"): "le-havre", ("fr", "angers"): "angers", ("fr", "auxerre"): "auxerre", ("fr", "lorient"): "lorient",
    ("fr", "metz"): "metz", ("fr", "paris-fc"): "paris-fc",
    ("nl", "for-sittard"): "fortuna-sittard", ("nl", "go-ahead-eagles"): "go-ahead-eagles", ("nl", "nijmegen"): "nec", ("nl", "waalwijk"): "rkc-waalwijk",
    ("nl", "zwolle"): "pec-zwolle", ("nl", "ajax"): "ajax", ("nl", "psv"): "psv", ("nl", "feyenoord"): "feyenoord", ("nl", "az-alkmaar"): "az-alkmaar",
    ("nl", "twente"): "twente", ("nl", "utrecht"): "utrecht", ("nl", "groningen"): "groningen", ("nl", "heerenveen"): "heerenveen",
    ("nl", "sparta-rotterdam"): "sparta-rotterdam", ("nl", "heracles"): "heracles-almelo", ("nl", "willem-ii"): "willem-ii", ("nl", "nac-breda"): "nac-breda",
    ("nl", "almere-city"): "almere-city", ("nl", "excelsior"): "excelsior", ("nl", "telstar"): "telstar", ("nl", "volendam"): "volendam",
    ("pt", "sp-lisbon"): "sporting-portugal", ("pt", "sp-braga"): "braga", ("pt", "guimaraes"): "vitoria-guimaraes", ("pt", "estoril"): "estoril-praia",
    ("pt", "famalicao"): "famalicao", ("pt", "porto"): "porto", ("pt", "benfica"): "benfica", ("pt", "rio-ave"): "rio-ave", ("pt", "gil-vicente"): "gil-vicente",
    ("pt", "casa-pia"): "casa-pia", ("pt", "arouca"): "arouca", ("pt", "moreirense"): "moreirense", ("pt", "nacional"): "nacional",
    ("pt", "santa-clara"): "santa-clara", ("pt", "estrela"): "estrela-amadora", ("pt", "boavista"): "boavista", ("pt", "farense"): "farense",
    ("pt", "avs"): "avs", ("pt", "tondela"): "tondela", ("pt", "alverca"): "alverca", ("pt", "maritimo"): "maritimo",
}


def _cid(country: str, name: str) -> str:
    from ..teams import normalise
    n = normalise(name)
    slug = FD_ALIASES.get((country, n))
    return f"{country}:{slug}" if slug else canonical_id(country, name)


def load_rows() -> list[dict]:
    rows = []
    for p in sorted(FD_DIR.glob("batch*.json")):
        for key, lines in json.loads(p.read_text()).items():
            season_code, div = key.split("/")
            comp = DIV_TO_COMP[div]
            season = f"20{season_code[:2]}-{season_code[2:]}"
            country = COUNTRY_OF_COMP[comp]
            for ln in lines:
                parts = ln.split("|")
                if len(parts) < 11:
                    continue
                date = datetime.strptime(parts[0], "%d/%m/%Y").date().isoformat()

                def f(x):
                    try:
                        return float(x)
                    except ValueError:
                        return None
                rows.append({"competition": comp, "season": season, "date_local": date, "home_raw": parts[1], "away_raw": parts[2],
                             "home_id": _cid(country, parts[1]), "away_id": _cid(country, parts[2]),
                             "fthg": int(parts[3]) if parts[3] else None, "ftag": int(parts[4]) if parts[4] else None,
                             "pinnacle": (f(parts[5]), f(parts[6])), "max": (f(parts[7]), f(parts[8])), "avg": (f(parts[9]), f(parts[10])),
                             "source_file": f"{p.name}:{key}"})
    return rows


def odds_snapshot_rows(fd_rows: list[dict], match_index: dict[tuple[str, str, str, str], str]) -> tuple[list[tuple], dict]:
    """Map football-data rows to dataset match ids and produce odds_snapshots tuples.
    match_index: (competition, date_local, home_id, away_id) -> match_id. Returns (rows, coverage report)."""
    out = []
    cov: dict[str, dict] = {}
    for r in fd_rows:
        key = f"{r['competition']}:{r['season']}"
        c = cov.setdefault(key, {"fd_rows": 0, "matched": 0, "unmatched_examples": [], "pinnacle_pairs": 0, "max_pairs": 0, "avg_pairs": 0,
                                 "score_mismatch": 0})
        c["fd_rows"] += 1
        mid = match_index.get((r["competition"], r["date_local"], r["home_id"], r["away_id"]))
        if mid is None:
            # football-data dates are the local match date; try +-1 day for late kick-offs listed on the following day
            for delta in (-1, 1):
                d = (datetime.fromisoformat(r["date_local"]).toordinal() + delta)
                d = datetime.fromordinal(d).date().isoformat()
                mid = match_index.get((r["competition"], d, r["home_id"], r["away_id"]))
                if mid:
                    break
        if mid is None:
            if len(c["unmatched_examples"]) < 5:
                c["unmatched_examples"].append(f"{r['date_local']} {r['home_raw']} v {r['away_raw']} -> {r['home_id']} v {r['away_id']}")
            continue
        c["matched"] += 1
        for book, key2 in (("Pinnacle", "pinnacle"), ("Max", "max"), ("Avg", "avg")):
            o, u = r[key2]
            if o and u and o > 1 and u > 1:
                c[f"{key2}_pairs"] += 1
                out.append((mid, book, "match_over", 2.5, "over", o, None, "closing", r["source_file"]))
                out.append((mid, book, "match_over", 2.5, "under", u, None, "closing", r["source_file"]))
    return out, cov
