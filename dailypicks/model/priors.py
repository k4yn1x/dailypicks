"""Promoted-team priors, estimated from TRAINING seasons only.

For every training league-season, teams absent from the previous season of the
same competition are "promoted". Their goals-for and goals-against rates in
that season are compared with the league average of the same season:

    attack_prior  = mean( log(GF_rate / league_rate) )
    defence_prior = mean( -log(GA_rate / league_rate) )   (higher = better defence)

The prior is applied through the L2 penalty centre during the promoted team's
first season in the competition (see dixon_coles.fit). It is computed with a
cutoff at the end of the training period, so validation/test seasons cannot
influence it. The result is cached in data/promoted_priors.json with the
seasons that were used.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone

from ..config import ACTIVE_COMPETITIONS, DATA_DIR, EVAL_SPLIT
from ..store import AsOfView

PRIORS_PATH = DATA_DIR / "promoted_priors.json"


def compute(competitions: list[str] | None = None) -> dict:
    competitions = competitions or ACTIVE_COMPETITIONS
    train = EVAL_SPLIT["train"]
    last_train_year = int(train[-1][:4]) + 1
    cutoff = datetime(last_train_year, 7, 1, tzinfo=timezone.utc)   # e.g. 2022-07-01 for train ending 2021-22
    view = AsOfView(cutoff)
    out = {"cutoff_utc": cutoff.isoformat(), "seasons_used": train, "per_competition": {}, "pooled": None}
    pooled_a, pooled_d = [], []
    for comp in competitions:
        rows = view.history([comp])
        by_season = defaultdict(list)
        for r in rows:
            by_season[r["season"]].append(r)
        la, ld, n_teams = [], [], 0
        for season in train:
            ms = by_season.get(season)
            if not ms:
                continue
            promoted = view.promoted_teams(comp, season)
            if not promoted:
                continue
            total_goals = sum(m["home_goals"] + m["away_goals"] for m in ms)
            league_rate = total_goals / (2 * len(ms))  # goals per team per match
            for t in promoted:
                gf = ga = n = 0
                for m in ms:
                    if m["home_id"] == t:
                        gf += m["home_goals"]; ga += m["away_goals"]; n += 1
                    elif m["away_id"] == t:
                        gf += m["away_goals"]; ga += m["home_goals"]; n += 1
                if n < 10:
                    continue
                la.append(math.log(max(gf, 1) / n / league_rate))
                ld.append(-math.log(max(ga, 1) / n / league_rate))
                n_teams += 1
        if la:
            out["per_competition"][comp] = {"attack": sum(la) / len(la), "defence": sum(ld) / len(ld), "n_team_seasons": n_teams}
            pooled_a += la; pooled_d += ld
    if pooled_a:
        out["pooled"] = {"attack": sum(pooled_a) / len(pooled_a), "defence": sum(pooled_d) / len(pooled_d), "n_team_seasons": len(pooled_a)}
    PRIORS_PATH.write_text(json.dumps(out, indent=2))
    return out


def load() -> dict:
    if not PRIORS_PATH.exists():
        return compute()
    return json.loads(PRIORS_PATH.read_text())


def prior_for(comp: str, priors: dict | None = None, min_n: int = 6) -> tuple[float, float] | None:
    priors = priors or load()
    pc = priors["per_competition"].get(comp)
    if pc and pc["n_team_seasons"] >= min_n:
        return (pc["attack"], pc["defence"])
    if priors.get("pooled"):
        return (priors["pooled"]["attack"], priors["pooled"]["defence"])
    return None


if __name__ == "__main__":
    print(json.dumps(compute(), indent=2))
