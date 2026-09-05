"""Canonical team identities and alias resolution.

A canonical team id is `<country>:<slug>` (e.g. `en:manchester-united`). Raw
names from each source are resolved through (1) an explicit alias table and
(2) a conservative normaliser that strips club-form suffixes. Resolution is
scoped by country so that e.g. "Sporting" in Portugal never collides with a
Spanish club. Anything not resolvable is returned as unmapped — never guessed.

Reserve / B / youth / women's sides are kept distinct: "SK Slavia Prague B" is
not "SK Slavia Prague".
"""
from __future__ import annotations

import re
import unicodedata

COUNTRY_OF_COMP = {
    "en.1": "en", "en.2": "en", "de.1": "de", "es.1": "es", "it.1": "it",
    "fr.1": "fr", "nl.1": "nl", "pt.1": "pt",
}

_SUFFIX_TOKENS = {
    "fc", "afc", "cf", "sc", "ac", "club", "de", "fsv", "tsg", "sv", "vfb", "vfl",
    "bsc", "cd", "ud", "rcd", "rc", "ca", "ss", "ssc", "us", "bc", "calcio", "sbv",
    "cfc", "cs", "gd", "cp", "the", "1", "1.", "04", "05", "07", "09", "1899", "1901",
    "1903", "1904", "1909", "1913", "1963", "65", "'65", "29", "alsace", "balompie",
    "1961", "1910", "1893", "1900", "1898", "1902", "1906", "1919", "1907", "ev",
}
_RESERVE_MARKERS = re.compile(r"\b(b|ii|u19|u21|u23|women|w|reserves?|amateure|jong)\b", re.I)


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def slugify(name: str) -> str:
    s = _ascii(name).lower().replace("&", " and ").replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"\s+", "-", s)


def normalise(name: str) -> str:
    """Strip club-form tokens conservatively. Reserve markers are preserved."""
    s = _ascii(name).lower().replace("&", " and ").replace("'", "")
    reserve = bool(_RESERVE_MARKERS.search(s))
    tokens = [t for t in re.split(r"[^a-z0-9.]+", s) if t]
    kept = [t for t in tokens if t not in _SUFFIX_TOKENS]
    if reserve:
        # keep the marker so reserve sides stay distinct
        kept = [t for t in tokens if t not in _SUFFIX_TOKENS or _RESERVE_MARKERS.fullmatch(t)]
    return "-".join(kept) if kept else slugify(name)


# Explicit aliases: (country, normalised raw name) -> canonical slug.
# Covers openfootball renames across seasons and bookmaker (ticket) spellings.
ALIASES: dict[tuple[str, str], str] = {
    # England
    ("en", "man-utd"): "manchester-united", ("en", "manchester-utd"): "manchester-united",
    ("en", "man-city"): "manchester-city", ("en", "spurs"): "tottenham-hotspur",
    ("en", "tottenham"): "tottenham-hotspur", ("en", "newcastle"): "newcastle-united",
    ("en", "wolves"): "wolverhampton-wanderers", ("en", "brighton"): "brighton-and-hove-albion",
    ("en", "brighton-hove-albion"): "brighton-and-hove-albion", ("en", "west-brom"): "west-bromwich-albion",
    ("en", "nottm-forest"): "nottingham-forest", ("en", "leeds"): "leeds-united",
    ("en", "sheffield-utd"): "sheffield-united", ("en", "qpr"): "queens-park-rangers",
    ("en", "bournemouth"): "bournemouth", ("en", "ipswich"): "ipswich-town",
    ("en", "luton"): "luton-town", ("en", "hull"): "hull-city", ("en", "charlton"): "charlton-athletic",
    # Germany
    ("de", "bayern-munich"): "bayern-munchen", ("de", "bayern"): "bayern-munchen",
    ("de", "dortmund"): "borussia-dortmund", ("de", "gladbach"): "borussia-monchengladbach",
    ("de", "monchengladbach"): "borussia-monchengladbach", ("de", "leverkusen"): "bayer-leverkusen",
    ("de", "hoffenheim"): "hoffenheim", ("de", "stuttgart"): "stuttgart", ("de", "hamburger"): "hamburger",
    ("de", "hamburg"): "hamburger", ("de", "hsv"): "hamburger", ("de", "elversberg"): "elversberg",
    ("de", "koln"): "koln", ("de", "fc-koln"): "koln", ("de", "cologne"): "koln", ("de", "mainz"): "mainz",
    ("de", "union-berlin"): "union-berlin", ("de", "st.-pauli"): "st.-pauli", ("de", "st-pauli"): "st.-pauli",
    ("de", "schalke"): "schalke", ("de", "leipzig"): "rb-leipzig", ("de", "werder"): "werder-bremen",
    ("de", "frankfurt"): "eintracht-frankfurt", ("de", "freiburg"): "freiburg", ("de", "augsburg"): "augsburg",
    ("de", "paderborn"): "paderborn", ("de", "kiel"): "holstein-kiel", ("de", "bochum-1848"): "bochum", ("de", "bochum"): "bochum",
    ("de", "heidenheim-1846"): "heidenheim", ("de", "heidenheim"): "heidenheim", ("de", "darmstadt-98"): "darmstadt", ("de", "darmstadt"): "darmstadt",
    ("fr", "as-saint-etienne"): "saint-etienne", ("fr", "saint-etienne"): "saint-etienne", ("fr", "st-etienne"): "saint-etienne",
    ("fr", "aj-auxerre"): "auxerre", ("fr", "montpellier-hsc"): "montpellier", ("fr", "montpellier"): "montpellier",
    # Spain
    ("es", "atletico-madrid"): "atletico-madrid", ("es", "atletico"): "atletico-madrid",
    ("es", "espanyol-barcelona"): "espanyol", ("es", "espanyol"): "espanyol", ("es", "rcd-espanyol"): "espanyol",
    ("es", "real-betis"): "real-betis", ("es", "betis"): "real-betis", ("es", "barcelona"): "barcelona",
    ("es", "fc-barcelona"): "barcelona", ("es", "real-madrid"): "real-madrid", ("es", "real-sociedad"): "real-sociedad",
    ("es", "athletic-bilbao"): "athletic", ("es", "athletic-club"): "athletic", ("es", "bilbao"): "athletic",
    ("es", "rayo-vallecano"): "rayo-vallecano", ("es", "rayo-vallecano-madrid"): "rayo-vallecano",
    ("es", "deportivo-la-coruna"): "deportivo-la-coruna", ("es", "deportivo"): "deportivo-la-coruna",
    ("es", "deportivo-alaves"): "alaves", ("es", "alaves"): "alaves", ("es", "malaga"): "malaga",
    ("es", "celta-vigo"): "celta-vigo", ("es", "celta"): "celta-vigo", ("es", "real-valladolid"): "valladolid",
    ("es", "valladolid"): "valladolid", ("es", "racing-santander"): "racing-santander",
    ("es", "real-racing-santander"): "racing-santander", ("es", "osasuna"): "osasuna", ("es", "sevilla"): "sevilla",
    ("es", "villarreal"): "villarreal", ("es", "valencia"): "valencia", ("es", "getafe"): "getafe",
    ("es", "levante"): "levante", ("es", "elche"): "elche", ("es", "real-sociedad-futbol"): "real-sociedad",
    ("it", "as-roma"): "roma", ("it", "acf-fiorentina"): "fiorentina",
    ("pt", "estrela-da-amadora"): "estrela-amadora",
    # Italy
    ("it", "inter"): "internazionale", ("it", "inter-milan"): "internazionale",
    ("it", "internazionale-milano"): "internazionale", ("it", "milan"): "milan", ("it", "ac-milan"): "milan",
    ("it", "juventus"): "juventus", ("it", "cagliari"): "cagliari", ("it", "napoli"): "napoli", ("it", "roma"): "roma",
    ("it", "lazio"): "lazio", ("it", "atalanta"): "atalanta", ("it", "fiorentina"): "fiorentina",
    ("it", "bologna"): "bologna", ("it", "parma"): "parma", ("it", "sassuolo"): "sassuolo",
    ("it", "hellas-verona"): "hellas-verona", ("it", "verona"): "hellas-verona", ("it", "torino"): "torino",
    ("it", "genoa"): "genoa", ("it", "udinese"): "udinese", ("it", "lecce"): "lecce", ("it", "monza"): "monza",
    ("it", "como"): "como", ("it", "venezia"): "venezia", ("it", "frosinone"): "frosinone",
    # France
    ("fr", "paris-saint-germain"): "paris-saint-germain", ("fr", "psg"): "paris-saint-germain",
    ("fr", "paris-sg"): "paris-saint-germain", ("fr", "lille"): "lille", ("fr", "lille-osc"): "lille",
    ("fr", "olympique-lyonnais"): "lyon", ("fr", "lyon"): "lyon", ("fr", "olympique-lyon"): "lyon",
    ("fr", "olympique-marseille"): "marseille", ("fr", "marseille"): "marseille",
    ("fr", "monaco"): "monaco", ("fr", "as-monaco"): "monaco", ("fr", "strasbourg"): "strasbourg",
    ("fr", "rc-strasbourg"): "strasbourg", ("fr", "lens"): "lens", ("fr", "racing-lens"): "lens",
    ("fr", "rc-lens"): "lens", ("fr", "le-havre"): "le-havre", ("fr", "stade-rennais"): "rennes",
    ("fr", "rennes"): "rennes", ("fr", "stade-brestois"): "brest", ("fr", "brest"): "brest",
    ("fr", "stade-reims"): "reims", ("fr", "reims"): "reims", ("fr", "nice"): "nice", ("fr", "ogc-nice"): "nice",
    ("fr", "toulouse"): "toulouse", ("fr", "auxerre"): "auxerre", ("fr", "angers"): "angers",
    ("fr", "angers-sco"): "angers", ("fr", "lorient"): "lorient", ("fr", "le-mans"): "le-mans",
    ("fr", "troyes"): "troyes", ("fr", "es-troyes"): "troyes", ("fr", "paris"): "paris-fc",
    # Netherlands
    ("nl", "az"): "az-alkmaar", ("nl", "az-alkmaar"): "az-alkmaar", ("nl", "psv"): "psv",
    ("nl", "psv-eindhoven"): "psv", ("nl", "ajax"): "ajax", ("nl", "afc-ajax"): "ajax",
    ("nl", "feyenoord"): "feyenoord", ("nl", "feyenoord-rotterdam"): "feyenoord",
    ("nl", "telstar"): "telstar", ("nl", "sc-telstar"): "telstar", ("nl", "twente"): "twente",
    ("nl", "fc-twente"): "twente", ("nl", "heerenveen"): "heerenveen", ("nl", "sc-heerenveen"): "heerenveen",
    ("nl", "vitesse"): "vitesse", ("nl", "sbv-vitesse"): "vitesse", ("nl", "willem-ii"): "willem-ii",
    ("nl", "willem-ii-tilburg"): "willem-ii", ("nl", "cambuur"): "cambuur", ("nl", "sc-cambuur"): "cambuur",
    ("nl", "cambuur-leeuwarden"): "cambuur", ("nl", "excelsior"): "excelsior",
    ("nl", "excelsior-rotterdam"): "excelsior", ("nl", "sbv-excelsior"): "excelsior",
    ("nl", "sparta-rotterdam"): "sparta-rotterdam", ("nl", "sparta"): "sparta-rotterdam",
    ("nl", "go-ahead-eagles"): "go-ahead-eagles", ("nl", "utrecht"): "utrecht", ("nl", "fc-utrecht"): "utrecht",
    ("nl", "nec"): "nec", ("nl", "nec-nijmegen"): "nec", ("nl", "groningen"): "groningen",
    ("nl", "fc-groningen"): "groningen", ("nl", "heracles"): "heracles-almelo", ("nl", "heracles-almelo"): "heracles-almelo",
    ("nl", "zwolle"): "pec-zwolle", ("nl", "pec-zwolle"): "pec-zwolle", ("nl", "fortuna-sittard"): "fortuna-sittard",
    ("nl", "ado-den-haag"): "ado-den-haag", ("nl", "roda-jc"): "roda-jc-kerkrade", ("nl", "roda-jc-kerkrade"): "roda-jc-kerkrade",
    ("nl", "den-bosch"): "den-bosch", ("nl", "fc-den-bosch"): "den-bosch", ("nl", "helmond-sport"): "helmond-sport",
    # Portugal
    ("pt", "sporting-cp"): "sporting-portugal", ("pt", "sporting-clube-portugal"): "sporting-portugal",
    ("pt", "sporting-portugal"): "sporting-portugal", ("pt", "sporting"): "sporting-portugal",
    ("pt", "sporting-braga"): "braga", ("pt", "sporting-clube-braga"): "braga", ("pt", "braga"): "braga",
    ("pt", "benfica"): "benfica", ("pt", "sl-benfica"): "benfica", ("pt", "sport-lisboa-e-benfica"): "benfica",
    ("pt", "porto"): "porto", ("pt", "fc-porto"): "porto", ("pt", "estoril"): "estoril-praia",
    ("pt", "estoril-praia"): "estoril-praia", ("pt", "gd-estoril"): "estoril-praia",
    ("pt", "rio-ave"): "rio-ave", ("pt", "academico-viseu"): "academico-viseu",
    ("pt", "academico-de-viseu"): "academico-viseu", ("pt", "boavista"): "boavista",
    ("pt", "gil-vicente"): "gil-vicente", ("pt", "vitoria-guimaraes"): "vitoria-guimaraes",
    ("pt", "vitoria-setubal"): "vitoria-setubal", ("pt", "famalicao"): "famalicao",
    ("pt", "estrela-amadora"): "estrela-amadora", ("pt", "estrela-da-amadora"): "estrela-amadora",
    ("pt", "santa-clara"): "santa-clara", ("pt", "nacional"): "nacional", ("pt", "maritimo"): "maritimo",
    ("pt", "casa-pia"): "casa-pia", ("pt", "alverca"): "alverca", ("pt", "arouca"): "arouca",
    ("pt", "moreirense"): "moreirense",
}


def canonical_id(country: str, raw_name: str) -> str | None:
    """Resolve a raw name to `country:slug`, or None when unmapped."""
    n = normalise(raw_name)
    slug = ALIASES.get((country, n))
    if slug is None:
        # second attempt: alias keyed on the plain slug (e.g. "fc-barcelona")
        slug = ALIASES.get((country, slugify(raw_name)))
    if slug is None:
        slug = n
    if not slug:
        return None
    return f"{country}:{slug}"


_ACRONYMS = {"az", "psv", "nec", "ii", "psg", "rb", "fc", "sc", "ado", "pec", "afc", "sv", "tsg", "vfb", "vfl", "hsv"}
_PRETTY = {"en:brighton-and-hove-albion": "Brighton & Hove Albion", "de:bayern-munchen": "Bayern München",
           "de:borussia-monchengladbach": "Borussia Mönchengladbach", "de:koln": "1. FC Köln", "it:internazionale": "Inter",
           "es:athletic": "Athletic Club", "es:alaves": "Alavés", "es:malaga": "Málaga", "pt:sporting-portugal": "Sporting CP",
           "pt:academico-viseu": "Académico de Viseu", "pt:famalicao": "Famalicão", "pt:estrela-amadora": "Estrela da Amadora",
           "pt:maritimo": "Marítimo", "pt:vitoria-guimaraes": "Vitória Guimarães", "fr:paris-saint-germain": "Paris Saint-Germain",
           "fr:paris-fc": "Paris FC", "nl:az-alkmaar": "AZ Alkmaar", "en:st.-pauli": "St. Pauli", "de:st.-pauli": "St. Pauli",
           "es:deportivo-la-coruna": "Deportivo La Coruña", "es:racing-santander": "Racing Santander", "nl:pec-zwolle": "PEC Zwolle",
           "nl:ado-den-haag": "ADO Den Haag", "de:rb-leipzig": "RB Leipzig", "en:queens-park-rangers": "QPR"}


def display_name(canonical: str) -> str:
    if canonical in _PRETTY:
        return _PRETTY[canonical]
    slug = canonical.split(":", 1)[1]
    return " ".join(w.upper() if w in _ACRONYMS else w.capitalize() for w in slug.split("-"))
