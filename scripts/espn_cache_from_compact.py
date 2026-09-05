"""Convert a compact ESPN scoreboard dump (league|YYYYMMDD -> ['date|home|away|state|desc|completed', ...])
into the cache files `dailypicks.sources.espn.load_cached` reads.

The compact dump is produced by running a small fetch() loop against the ESPN
endpoint in a browser (used when the execution environment cannot reach ESPN
directly). Provenance is recorded in each cache file.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dailypicks.sources.espn import CACHE_DIR, ESPN_LEAGUE  # noqa: E402

REV = {v: k for k, v in ESPN_LEAGUE.items()}


def convert(compact: dict, retrieved_at: str, provenance: str) -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for key, lines in compact.items():
        league, d = key.split("|")
        comp = REV[league]
        events = []
        if isinstance(lines, list):
            for ln in lines:
                parts = ln.split("|")
                if len(parts) == 5:  # pairing/status only (kick-off time not trusted from this source)
                    parts.insert(0, "")
                date, home, away, state, desc, completed = parts
                events.append({"date": date, "competitions": [{"competitors": [
                    {"homeAway": "home", "team": {"displayName": home}}, {"homeAway": "away", "team": {"displayName": away}}],
                    "status": {"type": {"state": state, "description": desc, "completed": completed == "1"}}}]})
        blob = {"retrieved_at": retrieved_at, "provenance": provenance, "data": {"events": events}}
        (CACHE_DIR / f"{d}_{comp}.json").write_text(json.dumps(blob))
        n += 1
    return n


if __name__ == "__main__":
    src = Path(sys.argv[1])
    compact = json.loads(src.read_text())
    print(convert(compact, datetime.now(timezone.utc).isoformat(), f"ESPN scoreboard JSON fetched in the user's browser pane; dump {src.name}"))
