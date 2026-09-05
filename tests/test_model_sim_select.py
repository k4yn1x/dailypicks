import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from scipy.optimize import check_grad

from dailypicks import config
from dailypicks.markets import MARKETS, Settlement, settle_all, settle_one
from dailypicks.model import dixon_coles as dc
from dailypicks.select import decide_fixture, qualifies, rank_and_cap
from dailypicks.simulate import simulate_fixture


def synthetic_history(T=12, n_seasons=3, seed=3, rho=-0.08, home=0.25, mu=0.1):
    rng = np.random.default_rng(seed)
    teams = [f"xx:t{i:02d}" for i in range(T)]
    att = rng.normal(0, 0.3, T); att -= att.mean(); dfn = rng.normal(0, 0.25, T); dfn -= dfn.mean()
    rows = []
    t0 = datetime(2022, 8, 1, tzinfo=timezone.utc)
    k = 0
    for s in range(n_seasons):
        for i in range(T):
            for j in range(T):
                if i == j:
                    continue
                lam = math.exp(mu + home + att[i] - dfn[j]); mua = math.exp(mu + att[j] - dfn[i])
                # sample from the DC joint by inverse CDF on a grid
                P, _ = dc.score_matrix(lam, mua, rho, max_goals=12)
                P = P / P.sum()
                idx = rng.choice(P.size, p=P.reshape(-1))
                x, y = divmod(idx, P.shape[1])
                ko = t0 + timedelta(days=365 * s + k % 200)
                rows.append({"home_id": teams[i], "away_id": teams[j], "home_goals": int(x), "away_goals": int(y),
                             "kickoff_utc": ko.isoformat(), "date_local": ko.date().isoformat()})
                k += 1
    return teams, att, dfn, rows


def test_gradient_matches_finite_differences():
    teams, att, dfn, rows = synthetic_history(T=6, n_seasons=1)
    idx = {t: k for k, t in enumerate(teams)}; T = len(teams)
    hi = np.array([idx[r["home_id"]] for r in rows]); ai = np.array([idx[r["away_id"]] for r in rows])
    x = np.array([r["home_goals"] for r in rows], float); y = np.array([r["away_goals"] for r in rows], float)
    args = (hi, ai, x, y, np.ones(len(rows)), T, 0.5, np.zeros(T), np.zeros(T), np.ones(T))
    th = np.random.default_rng(0).normal(0, 0.1, 2 * T + 3); th[-1] = -0.05
    f = lambda t: dc._neg_penalised_loglik(t, *args)[0]
    g = lambda t: dc._neg_penalised_loglik(t, *args)[1]
    rel = check_grad(f, g, th) / np.linalg.norm(g(th))
    assert rel < 1e-5


def test_parameter_recovery_on_synthetic_data():
    teams, att, dfn, rows = synthetic_history(T=12, n_seasons=3)
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fit = dc.fit("xx", rows, cutoff, hyper={"xi": 0.0, "l2": 0.05})
    assert fit.converged and fit.cov is not None
    a, d, mu, h, rho = fit.unpack()
    order = [fit.index(t) for t in teams]
    a, d = a[order], d[order]
    assert np.corrcoef(a - a.mean(), att)[0, 1] > 0.9
    assert np.corrcoef(d - d.mean(), dfn)[0, 1] > 0.85
    assert abs(h - 0.25) < 0.1
    assert abs(rho - (-0.08)) < 0.08


def test_fit_rejects_history_after_cutoff():
    teams, att, dfn, rows = synthetic_history(T=6, n_seasons=1)
    with pytest.raises(ValueError, match="leakage"):
        dc.fit("xx", rows, datetime(2021, 1, 1, tzinfo=timezone.utc))


def test_score_distribution_valid_and_not_renormalised():
    P, tail = dc.score_matrix(1.4, 1.1, -0.1)
    assert np.all(P >= 0) and np.isfinite(P).all()
    assert abs(P.sum() + tail - 1) < 1e-12 and tail < config.MODEL["tail_tolerance"]
    ok, _ = dc.valid_distribution(P, tail)
    assert ok
    # a huge rate on a tiny grid loses substantial mass -> must be flagged, not hidden
    P2, tail2 = dc.score_matrix(6.0, 0.5, -0.1, max_goals=5)
    ok2, why = dc.valid_distribution(P2, tail2)
    assert not ok2 and "tail" in why
    # invalid rho -> negative cell -> flagged
    P3, tail3 = dc.score_matrix(3.0, 3.0, -0.5, max_goals=20)
    ok3, why3 = dc.valid_distribution(P3, tail3)
    assert not ok3 and "negative" in why3


# ---------------------------------------------------------------- settlement
@pytest.mark.parametrize("market,line,h,a,expected", [
    ("match_over", 1.0, 2, 0, "win"), ("match_over", 1.0, 1, 0, "push"), ("match_over", 1.0, 0, 0, "loss"),
    ("match_over", 2.0, 2, 1, "win"), ("match_over", 2.0, 1, 1, "push"), ("match_over", 2.0, 1, 0, "loss"),
    ("match_over", 1.5, 1, 1, "win"), ("match_over", 1.5, 1, 0, "loss"), ("match_over", 2.5, 2, 1, "win"), ("match_over", 2.5, 1, 1, "loss"),
    ("home_over", 0.5, 1, 0, "win"), ("home_over", 0.5, 0, 3, "loss"), ("away_over", 1.5, 0, 2, "win"), ("away_over", 1.5, 3, 1, "loss"),
])
def test_settlement_rules(market, line, h, a, expected):
    assert settle_one(market, line, h, a) == expected


def test_survival_definition():
    s = Settlement("match_over", 1.0, 0.57, 0.23, 0.20, 10000)
    assert abs(s.survival - 0.80) < 1e-12 and s.p_win == 0.57  # never labelled as 80% win
    s2 = Settlement("match_over", 1.5, 0.57, 0.0, 0.43, 10000)
    assert s2.survival == 0.57


def test_markets_are_coherent_from_same_draws():
    rng = np.random.default_rng(1)
    h = rng.poisson(1.5, 20000); a = rng.poisson(1.1, 20000)
    st = {(s.market, s.line): s for s in settle_all(h, a)}
    assert st[("match_over", 2.0)].p_win == st[("match_over", 2.5)].p_win           # 3+ goals
    assert st[("match_over", 1.0)].p_win == st[("match_over", 1.5)].p_win           # 2+ goals
    assert st[("match_over", 2.0)].p_loss == st[("match_over", 1.5)].p_loss         # 0-1 goals
    assert st[("match_over", 0.5)].p_win == st[("match_over", 1.0)].survival        # 1+ goals
    for s in st.values():
        assert abs(s.p_win + s.p_push + s.p_loss - 1) < 1e-12


# ---------------------------------------------------------------- simulation
def test_simulation_reproducible_minimum_count_and_no_clipping():
    teams, att, dfn, rows = synthetic_history(T=10, n_seasons=2)
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fit = dc.fit("xx", rows, cutoff, hyper={"xi": 0.001, "l2": 1.0})
    s1 = simulate_fixture(fit, "m1", teams[0], teams[1])
    s2 = simulate_fixture(fit, "m1", teams[0], teams[1])
    assert s1.seed == s2.seed and np.array_equal(s1.home, s2.home) and np.array_equal(s1.away, s2.away)
    assert s1.valid == len(s1.home) == config.SIM["n"] == 10_000
    assert s1.drawn >= s1.valid + s1.invalid_tail + s1.invalid_negative + s1.invalid_nonfinite - 16  # accounting is explicit
    assert s1.home.max() <= config.MODEL["max_goals"]
    s3 = simulate_fixture(fit, "m2", teams[0], teams[1])
    assert s3.seed != s1.seed


# ---------------------------------------------------------------- selection policy
def S(market, line, win, push):
    return Settlement(market, line, win, push, 1 - win - push, 10000)


def test_eighty_percent_gate_is_unrounded():
    assert not qualifies(S("match_over", 1.5, 0.7999, 0.0))[0]
    assert qualifies(S("match_over", 1.5, 0.8000, 0.0))[0]
    assert qualifies(S("match_over", 2.0, 0.60, 0.20))[0]           # survival 0.80 with 60% win
    assert not qualifies(S("match_over", 2.0, 0.59, 0.30))[0]       # push-heavy line rejected by min outright win
    assert not qualifies(S("match_over", 2.0, 0.65, 0.10))[0]       # survival 0.75


def test_one_primary_per_fixture_and_priority_order():
    st = [S("match_over", 0.5, 0.97, 0), S("match_over", 1.0, 0.85, 0.10), S("match_over", 1.5, 0.85, 0), S("match_over", 2.0, 0.55, 0.30),
          S("match_over", 2.5, 0.55, 0), S("home_over", 0.5, 0.9, 0), S("home_over", 1.5, 0.6, 0), S("away_over", 0.5, 0.5, 0), S("away_over", 1.5, 0.2, 0)]
    d = decide_fixture("m", st, watchlist=False)
    assert d.status == "pick" and (d.primary.market, d.primary.line) == ("match_over", 1.5)
    assert all((s.market, s.line) != ("match_over", 1.5) for s in d.secondary)
    assert ("match_over", 2.0) not in [(s.market, s.line) for s in d.secondary]  # failed the min-win rule


def test_pass_when_nothing_qualifies_and_cap_and_no_minimum():
    d = decide_fixture("m", [S("match_over", 0.5, 0.79, 0)], watchlist=True)
    assert d.status == "pass" and "80" in d.reason or "survival" in d.reason
    decisions = [decide_fixture(f"m{i}", [S("match_over", 1.5, 0.81 + i * 0.001, 0)], watchlist=(i % 2 == 0)) for i in range(25)]
    rank_and_cap(decisions, {f"m{i}": "2026-01-01T00:00:00+00:00" for i in range(25)})
    picks = sorted([d for d in decisions if d.status == "pick"], key=lambda d: d.rank); capped = [d for d in decisions if d.status == "qualified_capped"]
    assert len(picks) == 20 and len(capped) == 5
    assert all(d.watchlist for d in picks[:13]) and not any(d.watchlist for d in capped)  # watchlist fixtures ranked first
    assert all("cap" in d.reason for d in capped)        # labelled as cap omissions, not failures
    few = [decide_fixture("a", [S("match_over", 1.5, 0.9, 0)], False)]
    rank_and_cap(few, {"a": "x"})
    assert sum(d.status == "pick" for d in few) == 1     # no forced minimum: one qualifying fixture -> one pick
