"""Walk-forward historical evaluation.

For each competition and evaluation season, every match date D becomes a
prediction cutoff (D 00:00 UTC — the morning of the matches). The model is
refitted with history available before that cutoff (AsOfView), and the
posterior-predictive market probabilities are computed from `draws` parameter
samples (same mechanism as the live simulation, without Monte Carlo score
sampling: grids are averaged analytically over the parameter draws).

Metrics per market (event = "over the line", i.e. WIN; for integer lines the
SURVIVAL event goals >= line is also scored):
  log loss, Brier, calibration curve (10 bins), calibration slope/intercept
  (logistic recalibration of logit(p)), sample counts and exclusions, and
  blocked-bootstrap 95% intervals (blocks = match dates).

Reference model: league-rate Poisson (mu + home advantage only, same decay)
— it answers "does the team structure add anything?". A bookmaker baseline is
only computed where timestamped closing odds exist in odds_snapshots.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

from .config import ACTIVE_COMPETITIONS, DATA_DIR, EVAL_SPLIT, MODEL, MODEL_VERSION
from .markets import MARKETS, is_integer_line, relevant_goals
from .model import dixon_coles as dc
from .model import priors as priors_mod
from .store import AsOfView, connect, DATASET_PATH

EVAL_DIR = DATA_DIR / "eval"


def _season_matches(comp: str, season: str) -> list[dict]:
    with connect(DATASET_PATH, readonly=True) as con:
        rows = con.execute("""SELECT m.match_id, m.date_local, m.kickoff_utc, m.home_id, m.away_id, r.home_goals, r.away_goals
                              FROM matches m JOIN results r ON r.match_id=m.match_id
                              WHERE m.competition=? AND m.season=? ORDER BY m.date_local""", (comp, season)).fetchall()
    return [dict(r) for r in rows]


def market_probs_from_grid(P: np.ndarray) -> dict[str, dict]:
    """Win/push probabilities for every market from an averaged score grid."""
    G = P.shape[0]
    h = np.repeat(np.arange(G), G); a = np.tile(np.arange(G), G)
    p = P.reshape(-1)
    out = {}
    for market, line in MARKETS:
        g = relevant_goals(market, h, a)
        win = float(p[g > line].sum())
        push = float(p[g == line].sum()) if is_integer_line(line) else 0.0
        out[f"{market}:{line}"] = {"win": win, "push": push}
    return out


def posterior_predictive_grid(fit: dc.FitResult, home: str, away: str, draws: int, seed: int) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(seed)
    L = np.linalg.cholesky(fit.cov + 1e-12 * np.eye(len(fit.theta)))
    z = rng.standard_normal((draws, len(fit.theta)))
    theta = fit.theta[None, :] + z @ L.T
    lam, mu, rho = fit.rates(home, away, theta)
    P, tail = dc.score_matrices(lam, mu, rho)
    good = np.isfinite(tail) & (tail <= MODEL["tail_tolerance"]) & (P >= 0).all(axis=(1, 2))
    if good.sum() == 0:
        raise ValueError("no valid parameter draws")
    Pm = P[good].mean(axis=0)
    return Pm, {"valid_draws": int(good.sum()), "draws": draws}


def walk_forward(comp: str, season: str, hyper: dict | None = None, draws: int = 500, reference: bool = True) -> list[dict]:
    matches = _season_matches(comp, season)
    priors = priors_mod.load()
    pprior = priors_mod.prior_for(comp, priors)
    by_date = defaultdict(list)
    for m in matches:
        by_date[m["date_local"]].append(m)
    out = []
    hp = dict(MODEL); hp.update(hyper or {})
    for date in sorted(by_date):
        cutoff = datetime.fromisoformat(date + "T00:00:00+00:00")
        view = AsOfView(cutoff)
        hist = view.history([comp], since_days=hp["history_days"])
        promoted = view.promoted_teams(comp, season)
        try:
            fit = dc.fit(comp, hist, cutoff, promoted=promoted, promoted_prior=pprior, hyper=hp)
        except Exception as e:  # noqa: BLE001
            for m in by_date[date]:
                out.append({**m, "excluded": f"fit error: {e}"})
            continue
        ref_fit = None
        if reference:
            ref_fit = dc.fit(comp, hist, cutoff, promoted=set(), promoted_prior=None,
                             hyper={**hp, "l2": 1e6})  # attack/defence pinned to 0 -> league-rate model
        for m in by_date[date]:
            rec = {"match_id": m["match_id"], "date": date, "home": m["home_id"], "away": m["away_id"],
                   "home_goals": m["home_goals"], "away_goals": m["away_goals"]}
            if not fit.converged:
                rec["excluded"] = "fit did not converge"; out.append(rec); continue
            cnt_h = fit.matches_per_team.get(m["home_id"], 0); cnt_a = fit.matches_per_team.get(m["away_id"], 0)
            if min(cnt_h, cnt_a) < hp["min_team_matches"]:
                rec["excluded"] = f"insufficient history ({cnt_h}/{cnt_a} matches)"; out.append(rec); continue
            seed = int.from_bytes(m["match_id"].encode()[-4:], "big") if len(m["match_id"]) >= 4 else 0
            try:
                Pm, info = posterior_predictive_grid(fit, m["home_id"], m["away_id"], draws, seed)
            except Exception as e:  # noqa: BLE001
                rec["excluded"] = f"simulation error: {e}"; out.append(rec); continue
            rec["model"] = market_probs_from_grid(Pm)
            rec["valid_draws"] = info["valid_draws"]
            lam, mu, rho = fit.rates(m["home_id"], m["away_id"])
            rec["lambda_home"], rec["lambda_away"] = float(lam), float(mu)
            if ref_fit is not None and ref_fit.converged:
                Pr, _ = dc.score_matrix(*[float(v) for v in ref_fit.rates(m["home_id"], m["away_id"])])
                rec["reference"] = market_probs_from_grid(Pr)
            out.append(rec)
    return out


# ------------------------------------------------------------------ metrics

def _clip(p):
    return np.clip(p, 1e-6, 1 - 1e-6)


def calibration(p: np.ndarray, y: np.ndarray, bins: int = 10) -> dict:
    edges = np.linspace(0, 1, bins + 1)
    curve = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if m.sum():
            curve.append({"bin": [float(lo), float(hi)], "n": int(m.sum()), "mean_pred": float(p[m].mean()), "obs_rate": float(y[m].mean())})
    # logistic recalibration y ~ a + b*logit(p): slope b, intercept a (Newton iterations)
    x = np.log(_clip(p) / (1 - _clip(p)))
    a, b = 0.0, 1.0
    for _ in range(50):
        eta = a + b * x; q = 1 / (1 + np.exp(-eta)); wgt = q * (1 - q)
        g = np.array([np.sum(y - q), np.sum((y - q) * x)])
        H = np.array([[np.sum(wgt), np.sum(wgt * x)], [np.sum(wgt * x), np.sum(wgt * x * x)]]) + 1e-9 * np.eye(2)
        step = np.linalg.solve(H, g)
        a += step[0]; b += step[1]
        if np.abs(step).max() < 1e-8:
            break
    return {"curve": curve, "slope": float(b), "intercept": float(a)}


def scores(p: np.ndarray, y: np.ndarray) -> dict:
    pc = _clip(p)
    return {"log_loss": float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc))),
            "brier": float(np.mean((p - y) ** 2)), "n": int(len(y)), "base_rate": float(y.mean())}


def blocked_bootstrap(p: np.ndarray, y: np.ndarray, blocks: np.ndarray, reps: int = 400, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    uniq = np.unique(blocks)
    idx_by_block = {b: np.where(blocks == b)[0] for b in uniq}
    ll, br = [], []
    for _ in range(reps):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([idx_by_block[b] for b in pick])
        s = scores(p[idx], y[idx]); ll.append(s["log_loss"]); br.append(s["brier"])
    return {"log_loss_ci95": [float(np.percentile(ll, 2.5)), float(np.percentile(ll, 97.5))],
            "brier_ci95": [float(np.percentile(br, 2.5)), float(np.percentile(br, 97.5))], "reps": reps, "blocks": int(len(uniq))}


def summarise(records: list[dict], bootstrap: bool = True) -> dict:
    rated = [r for r in records if "model" in r]
    excl = defaultdict(int)
    for r in records:
        if "excluded" in r:
            excl[r["excluded"].split(" (")[0]] += 1
    out = {"n_matches": len(records), "n_rated": len(rated), "exclusions": dict(excl), "markets": {}}
    if not rated:
        return out
    blocks = np.array([r["date"] for r in rated])
    hg = np.array([r["home_goals"] for r in rated]); ag = np.array([r["away_goals"] for r in rated])
    for market, line in MARKETS:
        key = f"{market}:{line}"
        g = relevant_goals(market, hg, ag)
        for event in (["win", "survival"] if is_integer_line(line) else ["win"]):
            y = (g > line).astype(float) if event == "win" else (g >= line).astype(float)
            p = np.array([r["model"][key]["win"] + (r["model"][key]["push"] if event == "survival" else 0) for r in rated])
            entry = {"model": scores(p, y), "calibration": calibration(p, y)}
            if bootstrap:
                entry["model"]["bootstrap"] = blocked_bootstrap(p, y, blocks)
            refs = [r for r in rated if "reference" in r]
            if refs:
                pr = np.array([r["reference"][key]["win"] + (r["reference"][key]["push"] if event == "survival" else 0) for r in refs])
                yr = y[[i for i, r in enumerate(rated) if "reference" in r]]
                entry["reference_league_rate"] = scores(pr, yr)
            out["markets"][f"{key}:{event}"] = entry
    return out


def run(split: str, competitions: list[str] | None = None, hyper: dict | None = None, tag: str = "", draws: int = 500) -> dict:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    competitions = competitions or ACTIVE_COMPETITIONS
    seasons = EVAL_SPLIT[split]
    report = {"split": split, "seasons": seasons, "hyper": {**MODEL, **(hyper or {})}, "model_version": MODEL_VERSION,
              "generated_at": datetime.now(timezone.utc).isoformat(), "by_league_season": {}, "pooled": None}
    all_records = []
    for comp in competitions:
        for season in seasons:
            recs = walk_forward(comp, season, hyper=hyper, draws=draws)
            if not recs:
                report["by_league_season"][f"{comp}:{season}"] = {"missing": True}
                continue
            for r in recs:
                r["competition"] = comp; r["season"] = season
            all_records += recs
            report["by_league_season"][f"{comp}:{season}"] = summarise(recs, bootstrap=False)
    report["pooled"] = summarise(all_records, bootstrap=True)
    by_comp = defaultdict(list)
    for r in all_records:
        by_comp[r["competition"]].append(r)
    report["by_league"] = {c: summarise(v, bootstrap=False) for c, v in by_comp.items()}
    name = f"{split}{('_' + tag) if tag else ''}.json"
    (EVAL_DIR / name).write_text(json.dumps(report, indent=1))
    (EVAL_DIR / name.replace(".json", "_records.json")).write_text(json.dumps(all_records))
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("split", choices=["validation", "test"])
    ap.add_argument("--xi", type=float); ap.add_argument("--l2", type=float); ap.add_argument("--tag", default="")
    ap.add_argument("--comps", default=""); ap.add_argument("--draws", type=int, default=500)
    a = ap.parse_args()
    hyper = {k: v for k, v in {"xi": a.xi, "l2": a.l2}.items() if v is not None}
    rep = run(a.split, competitions=a.comps.split(",") if a.comps else None, hyper=hyper, tag=a.tag, draws=a.draws)
    pooled = rep["pooled"]
    print(json.dumps({k: {"ll": v["model"]["log_loss"], "brier": v["model"]["brier"], "n": v["model"]["n"],
                          "ref_ll": v.get("reference_league_rate", {}).get("log_loss")}
                      for k, v in pooled["markets"].items()}, indent=1))
    print("exclusions", pooled["exclusions"], "rated", pooled["n_rated"], "of", pooled["n_matches"])
