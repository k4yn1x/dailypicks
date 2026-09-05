"""Daily refresh orchestration.

Stages (each recorded in the run log): scheduled -> started -> data_fetched ->
model_fitted -> simulations_completed -> validated -> published | failed.

* A lock file prevents overlapping runs.
* Ingestion rebuilds the dataset atomically; a failure leaves the previous
  dataset in place and the run is marked failed.
* The board is built into a candidate JSON, validated, then atomically
  replaced into data/published/latest.json (and a dated copy).
* Every rated fixture's market probabilities are written to the immutable
  ledger with the run id, cutoff, seed, simulation counts and versions.
"""
from __future__ import annotations

import fcntl
import json
import os
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from . import ingest, watchlist as wl
from .config import (ACTIVE_COMPETITIONS, COMPETITIONS, DATA_DIR, DISPLAY_TZ, MODEL, MODEL_VERSION, POLICY, POLICY_VERSION,
                     PUBLISHED_DIR, SIM, STALE_AFTER_HOURS)
from .markets import MARKETS, describe, settle_all, settle_one, settlement_text, subject
from .sources import oddsapi
from .model import dixon_coles as dc
from .model import priors as priors_mod
from .select import decide_fixture, rank_and_cap, policy_description
from .simulate import simulate_fixture
from .sources import espn
from .store import DATASET_PATH, LEDGER_PATH, AsOfView, connect, init_ledger, iso, utcnow
from .teams import display_name

LOCK_PATH = DATA_DIR / "run.lock"
LATEST_PATH = PUBLISHED_DIR / "latest.json"
BOARDS_DIR = PUBLISHED_DIR / "boards"
STATUS_PATH = PUBLISHED_DIR / "status.json"


class RunLog:
    def __init__(self, run_id: str, trigger: str, scheduled_for: str | None):
        self.run_id = run_id
        self.stages: dict[str, str] = {"scheduled": scheduled_for or iso(utcnow())}
        self.trigger = trigger
        init_ledger()
        with connect(LEDGER_PATH) as con:
            con.execute("INSERT INTO runs (run_id, trigger, scheduled_for_utc, started_utc, status, stages_json, model_version, policy_version) VALUES (?,?,?,?,?,?,?,?)",
                        (run_id, trigger, scheduled_for, iso(utcnow()), "started", json.dumps(self.stages), MODEL_VERSION, POLICY_VERSION))

    def stage(self, name: str, **extra):
        self.stages[name] = iso(utcnow())
        with connect(LEDGER_PATH) as con:
            con.execute("UPDATE runs SET status=?, stages_json=? WHERE run_id=?", (name, json.dumps(self.stages), self.run_id))
            if extra:
                sets = ", ".join(f"{k}=?" for k in extra)
                con.execute(f"UPDATE runs SET {sets} WHERE run_id=?", (*extra.values(), self.run_id))

    def fail(self, err: str):
        self.stages["failed"] = iso(utcnow())
        with connect(LEDGER_PATH) as con:
            con.execute("UPDATE runs SET status='failed', error=?, stages_json=?, finished_utc=? WHERE run_id=?",
                        (err, json.dumps(self.stages), iso(utcnow()), self.run_id))

    def finish(self, **extra):
        self.stages["published"] = iso(utcnow())
        with connect(LEDGER_PATH) as con:
            sets = ", ".join(f"{k}=?" for k in extra)
            con.execute(f"UPDATE runs SET status='published', stages_json=?, finished_utc=?{', ' + sets if sets else ''} WHERE run_id=?",
                        (json.dumps(self.stages), iso(utcnow()), *extra.values(), self.run_id))


def settle_open_predictions(now: datetime) -> int:
    """Fill settlement columns for open ledger rows whose match now has a result."""
    n = 0
    with connect(LEDGER_PATH) as led, connect(DATASET_PATH, readonly=True) as ds:
        rows = led.execute("SELECT prediction_id, match_id, market, line FROM predictions WHERE status='open'").fetchall()
        for r in rows:
            res = ds.execute("SELECT home_goals, away_goals FROM results WHERE match_id=?", (r["match_id"],)).fetchone()
            if not res:
                continue
            outcome = settle_one(r["market"], r["line"], res["home_goals"], res["away_goals"])
            led.execute("UPDATE predictions SET status='settled', settled_home=?, settled_away=?, settlement=?, settled_at_utc=? WHERE prediction_id=?",
                        (res["home_goals"], res["away_goals"], outcome, iso(now), r["prediction_id"]))
            n += 1
    return n


def _display_dt(kickoff_utc: str | None, tz: str) -> tuple[str | None, str | None]:
    if not kickoff_utc:
        return None, None
    dt = datetime.fromisoformat(kickoff_utc).astimezone(ZoneInfo(tz))
    return dt.date().isoformat(), dt.strftime("%H:%M")


def rate_fixture(fit: dc.FitResult | None, m, now: datetime, promoted_prior_used: bool) -> dict:
    """Apply data-quality gates, simulate, settle every market. Returns a fixture record (no selection yet)."""
    rec = {"match_id": m["match_id"], "competition": m["competition"], "competition_name": COMPETITIONS[m["competition"]]["name"],
           "season": m["season"], "home_id": m["home_id"], "away_id": m["away_id"],
           "home": display_name(m["home_id"]), "away": display_name(m["away_id"]),
           "home_raw": m["home_raw"], "away_raw": m["away_raw"], "kickoff_utc": m["kickoff_utc"], "date_local": m["date_local"],
           "rated": False, "reason": ""}
    if m["has_result"]:
        rec["reason"] = "match already completed"; return rec
    if not m["kickoff_utc"]:
        rec["reason"] = "kickoff time not yet published by the source (date only)"; return rec
    ko = datetime.fromisoformat(m["kickoff_utc"])
    if ko <= now + timedelta(minutes=POLICY["kickoff_buffer_minutes"]):
        rec["reason"] = "kick-off already passed or within the pre-kickoff buffer"; return rec
    if fit is None or not fit.converged:
        rec["reason"] = "competition model did not converge" if fit else "no model for competition"; return rec
    ch, ca = fit.matches_per_team.get(m["home_id"], 0), fit.matches_per_team.get(m["away_id"], 0)
    if min(ch, ca) < MODEL["min_team_matches"]:
        who = rec["home"] if ch < ca else rec["away"]
        rec["reason"] = f"insufficient history: {who} has {min(ch, ca)} matches in this competition within the {MODEL['history_days'] // 365}-year window (min {MODEL['min_team_matches']})"
        return rec
    sim = simulate_fixture(fit, m["match_id"], m["home_id"], m["away_id"])
    rec["sim"] = {"requested": sim.requested, "valid": sim.valid, "drawn": sim.drawn, "seed": sim.seed,
                  "invalid_tail": sim.invalid_tail, "invalid_negative": sim.invalid_negative, "invalid_nonfinite": sim.invalid_nonfinite,
                  "diagnostics": sim.diagnostics}
    if not sim.ok:
        rec["reason"] = f"only {sim.valid} valid simulations of {sim.requested} required"; return rec
    lam, mu, rho = fit.rates(m["home_id"], m["away_id"])
    rec["predicted_goals"] = {"home": round(float(lam), 3), "away": round(float(mu), 3), "rho": round(float(rho), 4)}
    P, tail = dc.score_matrix(float(lam), float(mu), float(rho))
    ok, why = dc.valid_distribution(P, tail)
    rec["distribution"] = {"tail_mass": float(tail), "valid": ok, "note": why}
    if not ok:
        rec["reason"] = f"invalid score distribution: {why}"; return rec
    settlements = settle_all(sim.home, sim.away)
    rec["markets"] = [{"market": s.market, "line": s.line, "label": describe(s.market, s.line, rec["home"], rec["away"]),
                       "p_win": s.p_win, "p_push": s.p_push, "p_loss": s.p_loss, "survival": s.survival, "mc_se": s.mc_se} for s in settlements]
    rec["_settlements"] = settlements
    rec["promoted_prior_applied"] = [t for t in (m["home_id"], m["away_id"]) if t in fit.promoted] if promoted_prior_used else []
    rec["rated"] = True
    return rec


def build_board(now: datetime, horizon_days: int = 7, cross_check: bool = True, log: RunLog | None = None, fetch_odds: bool = True) -> dict:
    view = AsOfView(now)
    odds_map, odds_status = ({}, {"source": "the-odds-api.com", "status": "not configured", "retrieved_at": None, "events": 0, "detail": ""})
    if fetch_odds and oddsapi.configured():
        try:
            odds_map, odds_status = oddsapi.fetch_fixture_odds(ACTIVE_COMPETITIONS)
        except Exception as e:  # noqa: BLE001
            odds_status = {"source": "the-odds-api.com", "status": "unavailable", "retrieved_at": None, "events": 0, "detail": f"{type(e).__name__}: {e}"[:200]}
    priors = priors_mod.load()
    watch = wl.supported_ids()
    fits: dict[str, dc.FitResult | None] = {}
    fit_info = {}
    for comp in ACTIVE_COMPETITIONS:
        hist = view.history([comp], since_days=MODEL["history_days"])
        season = view.season_of(comp)
        promoted = view.promoted_teams(comp, season) if season else set()
        try:
            fits[comp] = dc.fit(comp, hist, now, promoted=promoted, promoted_prior=priors_mod.prior_for(comp, priors))
            f = fits[comp]
            fit_info[comp] = {"converged": f.converged, "n_matches": f.n_matches, "effective_matches": round(f.effective_matches, 1),
                              "teams": f.T, "promoted": sorted(f.promoted), "season": season,
                              "mu": float(f.theta[2 * f.T]), "home_adv": float(f.theta[2 * f.T + 1]), "rho": float(f.theta[2 * f.T + 2]),
                              "message": f.message}
        except Exception as e:  # noqa: BLE001
            fits[comp] = None; fit_info[comp] = {"converged": False, "error": str(e), "season": season}
    if log:
        log.stage("model_fitted")
    upcoming = view.upcoming(ACTIVE_COMPETITIONS, horizon_days=horizon_days)
    fixtures = [rate_fixture(fits[m["competition"]], m, now, True) for m in upcoming]
    if log:
        log.stage("simulations_completed")
    # independent cross-check
    xcheck = {"source": "ESPN scoreboard", "status": "unavailable", "checked": 0, "verified": 0, "mismatch": 0, "detail": ""}
    if cross_check:
        cache: dict[tuple[str, str], list | None] = {}
        errors = []
        for fx in fixtures:
            key = (fx["competition"], fx["date_local"])
            if key not in cache:
                data = espn.load_cached(*key)
                if data is None:
                    try:
                        data = espn.fetch(*key)
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{key}: {type(e).__name__}"); data = None
                cache[key] = espn.events(fx["competition"], data) if data else None
            evs = cache[key]
            if evs is None:
                fx["verification"] = {"verified": None, "note": "cross-check source unreachable"}
                continue
            res = espn.cross_check(fx, evs)
            fx["verification"] = res
            xcheck["checked"] += 1
            if res["verified"]:
                xcheck["verified"] += 1
                st = (res.get("espn_state") or "")
                det = (res.get("espn_detail") or "").lower()
                if st == "post" or "postponed" in det or "cancel" in det:
                    fx["rated"] = False; fx["reason"] = f"independent source reports: {res.get('espn_detail')}"
                elif st == "in" and fx["rated"]:
                    fx["rated"] = False; fx["reason"] = "independent source reports the match in progress"
                if res.get("kickoff_match") is False and fx["rated"]:
                    fx["rated"] = False; fx["reason"] = f"kick-off time disagrees with independent source ({res.get('espn_kickoff_utc')})"
            else:
                xcheck["mismatch"] += 1
                other = espn.find_on_other_dates(fx, fx["competition"], fx["date_local"])
                if other:
                    res["espn_detail"] = f"independent source lists this fixture at {other} instead"
                if fx["rated"]:
                    fx["rated"] = False
                    fx["reason"] = (f"independent source schedules this fixture at {other}; the primary source date is unconfirmed"
                                    if other else "not confirmed by independent fixture source")
        checked_any = any(v is not None for v in cache.values())
        xcheck["status"] = "ok" if checked_any and not errors else ("partial" if checked_any else "unavailable")
        xcheck["detail"] = "; ".join(sorted(set(errors)))[:300]
    # selection per display day
    tz = DISPLAY_TZ
    today = now.astimezone(ZoneInfo(tz)).date()
    by_day: dict[str, list] = defaultdict(list)
    for fx in fixtures:
        d, t = _display_dt(fx["kickoff_utc"], tz)
        fx["display_date"], fx["display_time"] = (d or fx["date_local"]), t
        fx["watchlist"] = fx["home_id"] in watch or fx["away_id"] in watch
        by_day[fx["display_date"]].append(fx)
    days = []
    if today.isoformat() not in by_day:
        by_day[today.isoformat()] = []   # today with no remaining fixtures still gets a (empty) board entry
    for d in sorted(by_day):
        if d < today.isoformat():
            continue
        fxs = by_day[d]
        decisions = {}
        for fx in fxs:
            if fx["rated"]:
                prices = _prices_for(fx, odds_map, now)
                fx["odds"] = {"status": "available" if prices else odds_status["status"], "source": odds_status["source"],
                              "retrieved_at": odds_status.get("retrieved_at")}
                decisions[fx["match_id"]] = decide_fixture(fx["match_id"], fx["_settlements"], fx["watchlist"], prices)
        rank_and_cap(list(decisions.values()), {fx["match_id"]: fx["kickoff_utc"] for fx in fxs})
        for fx in fxs:
            dec = decisions.get(fx["match_id"])
            if dec is None:
                fx["status"] = "unrated"
            else:
                fx["status"] = dec.status; fx["reason"] = dec.reason; fx["rank"] = dec.rank; fx["basis"] = dec.basis
                fx["markets"] = [_mk(l, fx) for l in dec.lines]
                if dec.primary:
                    fx["primary"] = _mk(dec.primary, fx)
            fx.pop("_settlements", None)
        picks = sorted([fx for fx in fxs if fx["status"] == "pick"], key=lambda f: f["rank"])
        days.append({"date": d, "is_today": d == today.isoformat(), "provisional": d != today.isoformat(),
                     "n_fixtures": len(fxs), "n_rated": sum(1 for f in fxs if f.get("markets")), "n_picks": len(picks),
                     "n_qualified_capped": sum(1 for f in fxs if f["status"] == "qualified_capped"),
                     "n_pass": sum(1 for f in fxs if f["status"] == "pass"), "n_unrated": sum(1 for f in fxs if f["status"] == "unrated"),
                     "shortfall_note": _shortfall(len(picks), fxs),
                     "fixtures": sorted(fxs, key=lambda f: (f["kickoff_utc"] or "9", f["competition"]))})
    return {"days": days, "fits": fit_info, "cross_check": xcheck, "today": today.isoformat(), "odds_source": odds_status}


def _prices_for(fx: dict, odds_map: dict, now: datetime) -> dict:
    """Best available price per (market, line) for a fixture; stale prices (older than the policy limit) are kept but flagged."""
    rows = odds_map.get((fx["competition"], fx["home_id"], fx["away_id"]), {})
    out = {}
    for key, lst in rows.items():
        best = oddsapi.best_price(lst)
        if not best:
            continue
        stale = False
        try:
            age = now - datetime.fromisoformat(best["last_update"].replace("Z", "+00:00"))
            stale = age > timedelta(hours=POLICY["odds_max_age_hours"])
        except Exception:  # noqa: BLE001
            stale = True
        out[key] = {**best, "stale": stale, "n_bookmakers": len(lst)}
    return {k: v for k, v in out.items() if not v["stale"]} | {k: {**v, "excluded": "stale"} for k, v in out.items() if v["stale"]}


def _mk(l, fx):
    s = l.s
    pr = l.price
    return {"market": s.market, "line": s.line, "label": describe(s.market, s.line, fx["home"], fx["away"]),
            "subject": subject(s.market, fx["home"], fx["away"]), "settlement": settlement_text(s.market, s.line, fx["home"], fx["away"]),
            "p_win": s.p_win, "p_push": s.p_push, "p_loss": s.p_loss, "survival": s.survival, "mc_se": s.mc_se,
            "qualified": l.qualified, "reason": l.reason, "marginal": l.marginal, "break_even": l.break_even,
            "odds": ({"price": pr["price"], "bookmaker": pr["bookmaker"], "last_update": pr.get("last_update"), "retrieved_at": pr.get("retrieved_at"),
                      "n_bookmakers": pr.get("n_bookmakers"), "stale": pr.get("stale", False)} if pr else None),
            "ev": l.ev}


def _shortfall(n_picks: int, fxs: list) -> str:
    if not fxs:
        return "No supported fixtures remain today (all kicked off before this refresh, or none scheduled)."
    if n_picks >= POLICY["target_picks"]:
        return ""
    n_pass = sum(1 for f in fxs if f["status"] == "pass"); n_unrated = sum(1 for f in fxs if f["status"] == "unrated")
    return (f"{n_picks} of a {POLICY['target_picks']}-pick target: {len(fxs)} supported fixtures, "
            f"{n_pass} did not reach the 80% rule, {n_unrated} could not be rated. The threshold is never lowered to fill the card.")


def record_predictions(board: dict, run_id: str, now: datetime, data_commit: str) -> int:
    n = 0
    with connect(LEDGER_PATH) as con:
        for day in board["days"]:
            kind = "final" if day["is_today"] else "provisional"
            for fx in day["fixtures"]:
                if not fx.get("markets"):
                    continue
                prim = fx.get("primary")
                for mk in fx["markets"]:
                    is_primary = bool(prim and prim["market"] == mk["market"] and prim["line"] == mk["line"])
                    qualified = int(bool(mk.get("qualified")))
                    pid = f"{run_id}:{fx['match_id']}:{mk['market']}:{mk['line']}"
                    con.execute("""INSERT OR IGNORE INTO predictions (prediction_id, run_id, match_id, competition, home, away, kickoff_utc, predicted_at_utc, cutoff_utc,
                        model_version, policy_version, data_commit, sim_seed, sim_requested, sim_valid, market, line, p_win, p_push, p_loss, survival,
                        qualified, is_primary, reason, odds_json, board_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (pid, run_id, fx["match_id"], fx["competition"], fx["home"], fx["away"], fx["kickoff_utc"], iso(now), iso(now), MODEL_VERSION, POLICY_VERSION,
                                 data_commit, fx["sim"]["seed"], fx["sim"]["requested"], fx["sim"]["valid"], mk["market"], mk["line"],
                                 mk["p_win"], mk["p_push"], mk["p_loss"], mk["survival"], qualified, int(is_primary),
                                 fx["status"] + (": " + fx["reason"] if fx.get("reason") else ""),
                                 json.dumps({"odds": mk.get("odds"), "break_even": mk.get("break_even"), "ev": mk.get("ev")}), kind))
                    n += 1
                if fx["status"] in ("pick", "qualified_capped") and prim:
                    con.execute("INSERT OR REPLACE INTO selections VALUES (?,?,?,?,?,?,?,?)",
                                (run_id, day["date"], fx["match_id"], prim["market"], prim["line"], fx.get("rank"), fx["status"], fx.get("reason", "")))
    return n


def results_report() -> dict:
    """Prospective paper record from the ledger: final-board primary picks only, settled."""
    out = {"kind": "prospective_paper_record", "note": "Only final (same-day) primary picks count. No odds were recorded, so no ROI is reported.",
           "picks": {"win": 0, "push": 0, "loss": 0, "n": 0}, "by_market": {}, "by_league": {}, "predicted_vs_observed": None,
           "all_rated_markets": {"n": 0, "predicted_survival_mean": None, "observed_survival_rate": None}, "recent": []}
    with connect(LEDGER_PATH, readonly=True) as con:
        # the official pre-match record per fixture+market is the LAST final-board prediction written before kick-off
        rows = con.execute("""SELECT p.* FROM predictions p JOIN (
                                SELECT match_id, market, line, MAX(predicted_at_utc) AS last FROM predictions WHERE board_kind='final' GROUP BY match_id, market, line
                              ) l ON l.match_id=p.match_id AND l.market=p.market AND l.line=p.line AND l.last=p.predicted_at_utc
                              WHERE p.status='settled' AND p.board_kind='final'""").fetchall() if LEDGER_PATH.exists() else []
    picks = [r for r in rows if r["is_primary"] and r["qualified"] and r["reason"].startswith("pick")]
    surv_pred, surv_obs = [], []
    for r in picks:
        out["picks"][r["settlement"]] += 1; out["picks"]["n"] += 1
        key = f"{r['market']}:{r['line']}"
        bm = out["by_market"].setdefault(key, {"win": 0, "push": 0, "loss": 0, "n": 0}); bm[r["settlement"]] += 1; bm["n"] += 1
        bl = out["by_league"].setdefault(r["competition"], {"win": 0, "push": 0, "loss": 0, "n": 0}); bl[r["settlement"]] += 1; bl["n"] += 1
        surv_pred.append(r["survival"]); surv_obs.append(1.0 if r["settlement"] in ("win", "push") and (r["settlement"] == "win" or float(r["line"]).is_integer()) else 0.0)
    if picks:
        out["predicted_vs_observed"] = {"n": len(picks), "predicted_survival_mean": float(np.mean(surv_pred)), "observed_survival_rate": float(np.mean(surv_obs))}
    if rows:
        ps = [r["survival"] for r in rows]
        obs = [1.0 if (r["settlement"] == "win" or (r["settlement"] == "push" and float(r["line"]).is_integer())) else 0.0 for r in rows]
        out["all_rated_markets"] = {"n": len(rows), "predicted_survival_mean": float(np.mean(ps)), "observed_survival_rate": float(np.mean(obs))}
    with connect(LEDGER_PATH, readonly=True) as con:
        rec = con.execute("""SELECT p.match_id, p.competition, p.home, p.away, p.kickoff_utc, p.market, p.line, p.survival, p.p_win, p.p_push, p.settlement,
                                    p.settled_home, p.settled_away, p.run_id, p.predicted_at_utc
                             FROM predictions p JOIN (SELECT match_id, MAX(predicted_at_utc) AS last FROM predictions WHERE board_kind='final' AND is_primary=1 GROUP BY match_id) l
                               ON l.match_id=p.match_id AND l.last=p.predicted_at_utc
                             WHERE p.is_primary=1 AND p.board_kind='final' AND p.reason LIKE 'pick%' ORDER BY p.kickoff_utc DESC LIMIT 200""").fetchall()
    out["recent"] = [dict(r) for r in rec]
    return out


def coverage_report(board: dict, data_meta: dict) -> dict:
    with connect(DATASET_PATH, readonly=True) as con:
        seasons = {c: [dict(r) for r in con.execute("SELECT season, n_teams, n_matches, n_results, status, notes FROM season_validation WHERE competition=? ORDER BY season", (c,))]
                   for c in ACTIVE_COMPETITIONS}
    watch = json.loads(wl.WATCHLIST_PATH.read_text()) if wl.WATCHLIST_PATH.exists() else wl.build()
    comps = []
    for c in ACTIVE_COMPETITIONS:
        s = seasons[c]
        cur = s[-1] if s else None
        comps.append({"code": c, **COMPETITIONS[c], "seasons": len(s), "first_season": s[0]["season"] if s else None,
                      "current_season": cur, "fit": board["fits"].get(c)})
    return {"competitions": comps, "watchlist": watch, "source": {"primary": "openfootball/football.json (GitHub, public domain)",
            "commit": data_meta.get("data_commit"), "ingested_at": data_meta.get("ingested_at")},
            "missing_sources": ["Bookmaker odds / Pinnacle closing lines (football-data.co.uk) — not reachable from the current execution environment; value and market-baseline metrics are therefore unavailable",
                                "Leagues outside the eight ingested competitions (see watchlist 'awaiting coverage')"]}


def validate_published(doc: dict) -> None:
    assert doc["days"], "no board days"
    for day in doc["days"]:
        ids = [f["match_id"] for f in day["fixtures"]]
        assert len(ids) == len(set(ids)), "duplicate fixtures on a day"
        picks = [f for f in day["fixtures"] if f["status"] == "pick"]
        assert len(picks) == day["n_picks"] <= POLICY["max_picks"], "pick count/cap violated"
        for f in picks:
            p = f["primary"]
            assert p["survival"] >= POLICY["survival_threshold"] - 1e-12, "pick below threshold"
            assert f["sim"]["valid"] >= SIM["n"], "pick with insufficient simulations"
            assert all(0 <= p[k] <= 1 for k in ("p_win", "p_push", "p_loss")), "invalid probabilities"
            assert abs(p["p_win"] + p["p_push"] + p["p_loss"] - 1) < 1e-9, "probabilities do not sum to one"
        for f in day["fixtures"]:
            if f.get("markets"):
                for mk in f["markets"]:
                    assert abs(mk["p_win"] + mk["p_push"] + mk["p_loss"] - 1) < 1e-9


def atomic_write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=None, default=_json_default))
    os.replace(tmp, path)


def _json_default(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(str(type(o)))


def load_status() -> dict:
    return json.loads(STATUS_PATH.read_text()) if STATUS_PATH.exists() else {}


def run_daily(now: datetime | None = None, trigger: str = "manual", offline: bool = False, horizon_days: int = 7,
              cross_check: bool = True, scheduled_for: str | None = None) -> dict:
    now = now or utcnow()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return {"status": "skipped", "reason": "another run holds the lock"}
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    log = RunLog(run_id, trigger, scheduled_for)
    try:
        ing = ingest.run(offline=offline)
        log.stage("data_fetched", data_commit=ing["commit"]["commit"])
        with connect(DATASET_PATH, readonly=True) as con:
            data_meta = {r["key"]: json.loads(r["value"]) for r in con.execute("SELECT key, value FROM meta")}
        wl.build()
        n_settled = settle_open_predictions(now)
        board = build_board(now, horizon_days=horizon_days, cross_check=cross_check, log=log)
        n_pred = record_predictions(board, run_id, now, ing["commit"]["commit"])
        doc = {
            "generated_at_utc": iso(now), "run_id": run_id, "display_tz": DISPLAY_TZ, "today": board["today"],
            "data_cutoff_utc": iso(now), "data_commit": ing["commit"], "cross_check": board["cross_check"], "odds_source": board["odds_source"],
            "model": {"version": MODEL_VERSION, "hyper": MODEL, "sim": SIM, "fits": board["fits"]},
            "policy": policy_description(),
            "days": board["days"],
            "coverage": coverage_report(board, data_meta),
            "results": results_report(),
            "evaluation": _evaluation_summary(),
            "run": {"trigger": trigger, "stages": log.stages, "n_predictions_recorded": n_pred, "n_settled_this_run": n_settled},
        }
        validate_published(doc)
        log.stage("validated")
        atomic_write_json(LATEST_PATH, doc)
        atomic_write_json(BOARDS_DIR / f"{board['today']}.json", doc)
        status = {"last_success_utc": iso(now), "run_id": run_id, "status": "published", "stale_after_hours": STALE_AFTER_HOURS,
                  "n_picks_today": board["days"][0]["n_picks"] if board["days"] and board["days"][0]["is_today"] else 0, "error": None,
                  "last_attempt_utc": iso(now)}
        atomic_write_json(STATUS_PATH, status)
        log.finish(board_date=board["today"], n_picks=status["n_picks_today"])
        return {"status": "published", "run_id": run_id, "today": board["today"], "n_picks": status["n_picks_today"],
                "days": [(d["date"], d["n_fixtures"], d["n_picks"]) for d in board["days"]], "cross_check": board["cross_check"]}
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
        log.fail(err)
        prev = load_status()
        prev.update({"status": "failed", "error": err.splitlines()[0], "last_attempt_utc": iso(now), "failed_run_id": run_id})
        atomic_write_json(STATUS_PATH, prev)  # previous latest.json is preserved untouched
        return {"status": "failed", "run_id": run_id, "error": err}
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN); lock.close()


def _evaluation_summary() -> dict | None:
    p = DATA_DIR / "eval" / "test_frozen.json"
    if not p.exists():
        return None
    rep = json.loads(p.read_text())
    pooled = rep["pooled"]
    return {"split": rep["split"], "seasons": rep["seasons"], "hyper": rep["hyper"], "generated_at": rep["generated_at"],
            "n_rated": pooled["n_rated"], "n_matches": pooled["n_matches"], "exclusions": pooled["exclusions"],
            "markets": {k: {"log_loss": v["model"]["log_loss"], "brier": v["model"]["brier"], "n": v["model"]["n"], "base_rate": v["model"]["base_rate"],
                            "ci": v["model"].get("bootstrap"), "calibration": v["calibration"],
                            "reference_league_rate": v.get("reference_league_rate")} for k, v in pooled["markets"].items()},
            "by_league": {c: {k: {"log_loss": v["model"]["log_loss"], "brier": v["model"]["brier"], "n": v["model"]["n"],
                                  "reference_log_loss": v.get("reference_league_rate", {}).get("log_loss")}
                              for k, v in s["markets"].items()} for c, s in rep.get("by_league", {}).items()},
            "market_baseline": rep.get("market_baseline") or {"status": "unavailable", "reason": "No timestamped closing odds available. No betting edge is claimed."}}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true"); ap.add_argument("--no-cross-check", action="store_true")
    ap.add_argument("--trigger", default="manual"); ap.add_argument("--now", default=None)
    a = ap.parse_args()
    now = datetime.fromisoformat(a.now) if a.now else None
    print(json.dumps(run_daily(now=now, trigger=a.trigger, offline=a.offline, cross_check=not a.no_cross_check), indent=1, default=str))
