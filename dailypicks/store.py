"""Canonical storage.

Two SQLite files with different lifecycles:

* dataset.sqlite  — matches, results, odds snapshots. Rebuilt into a staging
  file, validated, then atomically swapped into place (os.replace). A failed
  rebuild leaves the previous file untouched.
* ledger.sqlite   — immutable prediction records, selections, run log.
  Append-only; rows are never updated after kickoff (enforced by trigger).

Model code must not read results directly: it goes through `AsOfView`, which
only exposes matches whose result was available before the requested cutoff.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import COMPETITIONS, DATA_DIR

DATASET_PATH = DATA_DIR / "dataset.sqlite"
LEDGER_PATH = DATA_DIR / "ledger.sqlite"

RESULT_AVAILABILITY_LAG = timedelta(hours=3)  # a result is "known" 3h after kickoff

DATASET_SCHEMA = """
CREATE TABLE IF NOT EXISTS archives (
  archive_id TEXT PRIMARY KEY, source TEXT, url TEXT, retrieved_at TEXT, sha256 TEXT,
  competition TEXT, season TEXT, schema_version TEXT, real_data INTEGER, meta_json TEXT);
CREATE TABLE IF NOT EXISTS matches (
  match_id TEXT PRIMARY KEY, competition TEXT NOT NULL, season TEXT NOT NULL, round TEXT,
  date_local TEXT NOT NULL, time_local TEXT, kickoff_utc TEXT,
  home_raw TEXT NOT NULL, away_raw TEXT NOT NULL, home_id TEXT NOT NULL, away_id TEXT NOT NULL,
  archive_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_matches_comp_date ON matches(competition, date_local);
CREATE INDEX IF NOT EXISTS ix_matches_kick ON matches(kickoff_utc);
CREATE TABLE IF NOT EXISTS results (
  match_id TEXT PRIMARY KEY REFERENCES matches(match_id),
  home_goals INTEGER NOT NULL, away_goals INTEGER NOT NULL, ht_home INTEGER, ht_away INTEGER,
  result_available_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS odds_snapshots (
  match_id TEXT, bookmaker TEXT, market TEXT, line REAL, side TEXT, price REAL,
  snapshot_utc TEXT, kind TEXT, source TEXT,
  PRIMARY KEY (match_id, bookmaker, market, line, side, kind));
CREATE TABLE IF NOT EXISTS season_validation (
  competition TEXT, season TEXT, n_teams INTEGER, n_matches INTEGER, expected_matches INTEGER,
  n_results INTEGER, status TEXT, notes TEXT, PRIMARY KEY (competition, season));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
  prediction_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, match_id TEXT NOT NULL,
  competition TEXT, kickoff_utc TEXT, predicted_at_utc TEXT NOT NULL, cutoff_utc TEXT NOT NULL,
  model_version TEXT NOT NULL, policy_version TEXT NOT NULL, data_commit TEXT,
  sim_seed INTEGER, sim_requested INTEGER, sim_valid INTEGER,
  market TEXT NOT NULL, line REAL NOT NULL, p_win REAL, p_push REAL, p_loss REAL, survival REAL,
  qualified INTEGER, is_primary INTEGER, reason TEXT, odds_json TEXT, board_kind TEXT NOT NULL DEFAULT 'final',
  status TEXT NOT NULL DEFAULT 'open', settled_home INTEGER, settled_away INTEGER, settlement TEXT,
  settled_at_utc TEXT);
CREATE INDEX IF NOT EXISTS ix_pred_match ON predictions(match_id);
CREATE INDEX IF NOT EXISTS ix_pred_run ON predictions(run_id);
-- Immutability: pre-match fields may never change; only settlement columns may be filled once.
CREATE TRIGGER IF NOT EXISTS predictions_immutable BEFORE UPDATE ON predictions
BEGIN
  SELECT CASE WHEN
    NEW.match_id != OLD.match_id OR NEW.market != OLD.market OR NEW.line != OLD.line OR
    NEW.p_win IS NOT OLD.p_win OR NEW.p_push IS NOT OLD.p_push OR NEW.p_loss IS NOT OLD.p_loss OR
    NEW.survival IS NOT OLD.survival OR NEW.qualified IS NOT OLD.qualified OR
    NEW.is_primary IS NOT OLD.is_primary OR NEW.predicted_at_utc != OLD.predicted_at_utc OR
    NEW.cutoff_utc != OLD.cutoff_utc OR NEW.sim_seed IS NOT OLD.sim_seed OR
    NEW.sim_valid IS NOT OLD.sim_valid OR NEW.model_version != OLD.model_version OR
    NEW.policy_version != OLD.policy_version OR NEW.reason IS NOT OLD.reason OR
    NEW.odds_json IS NOT OLD.odds_json OR NEW.kickoff_utc IS NOT OLD.kickoff_utc OR NEW.board_kind != OLD.board_kind
  THEN RAISE(ABORT, 'prediction records are immutable') END;
  SELECT CASE WHEN OLD.status = 'settled' THEN RAISE(ABORT, 'prediction already settled') END;
END;
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, trigger TEXT, scheduled_for_utc TEXT, started_utc TEXT, finished_utc TEXT,
  status TEXT NOT NULL, stages_json TEXT, error TEXT, board_date TEXT, n_picks INTEGER,
  data_commit TEXT, model_version TEXT, policy_version TEXT);
CREATE TABLE IF NOT EXISTS selections (
  run_id TEXT, board_date TEXT, match_id TEXT, market TEXT, line REAL, rank INTEGER,
  status TEXT, reason TEXT, PRIMARY KEY (run_id, match_id));
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@contextmanager
def connect(path: Path, readonly: bool = False):
    uri = f"file:{path}?mode=ro" if readonly else f"file:{path}"
    con = sqlite3.connect(uri, uri=True, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        yield con
        if not readonly:
            con.commit()
    finally:
        con.close()


# ------------------------------------------------------------------ dataset build

class ValidationError(Exception):
    pass


def validate_season(competition: str, season: str, rows: list[dict]) -> dict:
    """Structural validation of one league-season. Raises on hard failures."""
    ids = [r["match_id"] for r in rows]
    dup = [k for k, v in Counter(ids).items() if v > 1]
    if dup:
        raise ValidationError(f"{competition} {season}: duplicate match ids {dup[:3]}")
    unmapped = sorted({r["home_raw"] for r in rows if not r["home_id"]} | {r["away_raw"] for r in rows if not r["away_id"]})
    if unmapped:
        raise ValidationError(f"{competition} {season}: unmapped teams {unmapped}")
    raw_by_id: dict[str, set] = defaultdict(set)
    for r in rows:
        raw_by_id[r["home_id"]].add(r["home_raw"])
        raw_by_id[r["away_id"]].add(r["away_raw"])
    collisions = {k: v for k, v in raw_by_id.items() if len(v) > 1}
    if collisions:
        raise ValidationError(f"{competition} {season}: alias collision within season {collisions}")
    teams = set(raw_by_id)
    n = len(teams)
    pair_counts = Counter((r["home_id"], r["away_id"]) for r in rows)
    over = [k for k, v in pair_counts.items() if v > 1]
    if over:
        raise ValidationError(f"{competition} {season}: repeated home/away pairing {over[:3]}")
    for r in rows:
        if r["home_id"] == r["away_id"]:
            raise ValidationError(f"{competition} {season}: team plays itself {r['match_id']}")
        datetime.fromisoformat(r["date_local"])  # raises if malformed
        hg, ag = r["home_goals"], r["away_goals"]
        if (hg is None) != (ag is None):
            raise ValidationError(f"{competition} {season}: half-recorded score {r['match_id']}")
        if hg is not None and (hg < 0 or ag < 0 or hg > 20 or ag > 20):
            raise ValidationError(f"{competition} {season}: implausible score {r['match_id']} {hg}-{ag}")
    expected = n * (n - 1)
    n_results = sum(1 for r in rows if r["home_goals"] is not None)
    notes = []
    status = "ok"
    exp_teams = COMPETITIONS[competition]["teams"]
    if n != exp_teams:
        notes.append(f"{n} teams (configured current format {exp_teams})")
    if len(rows) != expected:
        status = "irregular"
        notes.append(f"{len(rows)} matches vs {expected} for a full double round-robin")
    return {"competition": competition, "season": season, "n_teams": n, "n_matches": len(rows),
            "expected_matches": expected, "n_results": n_results, "status": status, "notes": "; ".join(notes)}


def build_dataset(all_rows: dict[tuple[str, str], list[dict]], archives: list[dict], meta: dict) -> Path:
    """Write a staging dataset, validate, and atomically replace the active one."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    staging = DATASET_PATH.with_suffix(".sqlite.staging")
    if staging.exists():
        staging.unlink()
    validations = []
    for (comp, season), rows in all_rows.items():
        validations.append(validate_season(comp, season, rows))  # raises -> staging discarded
    with connect(staging) as con:
        con.executescript(DATASET_SCHEMA)
        for a in archives:
            con.execute("INSERT OR REPLACE INTO archives VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (a["archive_id"], a["source"], a["url"], a["retrieved_at"], a["sha256"],
                         a["competition"], a["season"], a["schema_version"], int(a["real_data"]), json.dumps(a)))
        for rows in all_rows.values():
            for r in rows:
                con.execute("INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (r["match_id"], r["competition"], r["season"], r["round"], r["date_local"], r["time_local"],
                             r["kickoff_utc"], r["home_raw"], r["away_raw"], r["home_id"], r["away_id"], r["archive_id"]))
                if r["home_goals"] is not None:
                    if r["kickoff_utc"]:
                        avail = datetime.fromisoformat(r["kickoff_utc"]) + RESULT_AVAILABILITY_LAG
                    else:  # date-only: assume end of that local day + lag (conservative: later)
                        avail = datetime.fromisoformat(r["date_local"] + "T23:59:00+00:00") + RESULT_AVAILABILITY_LAG
                    con.execute("INSERT INTO results VALUES (?,?,?,?,?,?)",
                                (r["match_id"], r["home_goals"], r["away_goals"], r["ht_home"], r["ht_away"], iso(avail)))
        for v in validations:
            con.execute("INSERT INTO season_validation VALUES (?,?,?,?,?,?,?,?)",
                        (v["competition"], v["season"], v["n_teams"], v["n_matches"], v["expected_matches"],
                         v["n_results"], v["status"], v["notes"]))
        for k, val in meta.items():
            con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, json.dumps(val)))
        # integrity re-check inside the staging file
        n = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        if n == 0:
            raise ValidationError("staging dataset has no matches")
        con.execute("PRAGMA integrity_check")
    os.replace(staging, DATASET_PATH)  # atomic on POSIX
    return DATASET_PATH


def init_ledger() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect(LEDGER_PATH) as con:
        con.executescript(LEDGER_SCHEMA)


# ------------------------------------------------------------------ as-of access

class AsOfView:
    """Leakage-guarded read interface used by model code.

    `cutoff_utc` is the prediction cutoff; only results available (kickoff+lag)
    strictly before the cutoff are visible. Fixtures returned by
    `upcoming()` are those kicking off after the cutoff.
    """

    def __init__(self, cutoff_utc: datetime, path: Path | None = None):
        if cutoff_utc.tzinfo is None:
            raise ValueError("cutoff must be timezone-aware")
        self.cutoff = cutoff_utc.astimezone(timezone.utc)
        self.path = path or DATASET_PATH

    def history(self, competitions: list[str], since_days: int | None = None) -> list[sqlite3.Row]:
        q = """SELECT m.match_id, m.competition, m.season, m.date_local, m.kickoff_utc, m.home_id, m.away_id,
                      r.home_goals, r.away_goals
               FROM matches m JOIN results r ON r.match_id = m.match_id
               WHERE m.competition IN (%s) AND r.result_available_utc < ?""" % ",".join("?" * len(competitions))
        params: list = [*competitions, iso(self.cutoff)]
        if since_days:
            q += " AND m.date_local >= ?"
            params.append((self.cutoff - timedelta(days=since_days)).date().isoformat())
        q += " ORDER BY m.date_local, m.kickoff_utc"
        with connect(self.path, readonly=True) as con:
            return con.execute(q, params).fetchall()

    def upcoming(self, competitions: list[str], horizon_days: int = 8) -> list[sqlite3.Row]:
        q = """SELECT m.*, (r.match_id IS NOT NULL) AS has_result
               FROM matches m LEFT JOIN results r ON r.match_id = m.match_id
               WHERE m.competition IN (%s) AND m.date_local >= ? AND m.date_local <= ?
               ORDER BY m.date_local, m.kickoff_utc""" % ",".join("?" * len(competitions))
        d0 = (self.cutoff - timedelta(days=1)).date().isoformat()
        d1 = (self.cutoff + timedelta(days=horizon_days)).date().isoformat()
        with connect(self.path, readonly=True) as con:
            rows = con.execute(q, [*competitions, d0, d1]).fetchall()
        out = []
        for m in rows:
            if m["kickoff_utc"] and datetime.fromisoformat(m["kickoff_utc"]) <= self.cutoff:
                continue  # already kicked off relative to the cutoff
            out.append(m)
        return out

    def season_of(self, competition: str) -> str | None:
        with connect(self.path, readonly=True) as con:
            r = con.execute("SELECT season FROM matches WHERE competition=? AND date_local<=? ORDER BY date_local DESC LIMIT 1",
                            (competition, self.cutoff.date().isoformat())).fetchone()
        return r["season"] if r else None

    def promoted_teams(self, competition: str, season: str) -> set[str]:
        """Teams in `season` that were absent from the previous season of the same competition.
        Uses only schedule membership (known before the season starts)."""
        with connect(self.path, readonly=True) as con:
            cur = {r[0] for r in con.execute("SELECT DISTINCT home_id FROM matches WHERE competition=? AND season=?", (competition, season))}
            prev_season = con.execute("SELECT DISTINCT season FROM matches WHERE competition=? AND season<? ORDER BY season DESC LIMIT 1",
                                      (competition, season)).fetchone()
            if not prev_season:
                return set()
            prev = {r[0] for r in con.execute("SELECT DISTINCT home_id FROM matches WHERE competition=? AND season=?", (competition, prev_season[0]))}
        return cur - prev
