"""Daily selection policy (POLICY_VERSION in config) — select-2.0.0, odds-aware.

Lines evaluated per fixture (all from the same simulated score pairs):
  total match goals Over 0.5 / 1.0 / 1.5 / 2.0 / 2.5, and the same five lines
  for the home team's goals and for the away team's goals (15 lines).

1. A line QUALIFIES when unrounded survival >= 0.80 (survival = win, plus push
   for whole-goal lines) AND, for whole-goal lines, unrounded outright win
   >= 0.60 (documented minimum so push-heavy lines cannot dominate).
2. For every line: break-even decimal odds = 1 + p_loss / p_win (model
   estimate); if a bookmaker price d exists: EV = p_win * (d - 1) - p_loss.
3. PRIMARY line (exactly one per fixture, or PASS):
   - if any qualifying line has a real price: the qualifying line with the
     highest EV (ties: higher survival);
   - otherwise: the qualifying line with the highest model break-even price
     (i.e. the qualifying line where the model's edge is worth most per unit),
     ties: higher survival. This deliberately does NOT default to the lowest
     goal line just because it has the highest survival, and it never reaches
     for a line that fails the rule just to raise the odds.
   Model uncertainty enters through the posterior-predictive simulation itself
   (parameter uncertainty widens every probability) and the Monte Carlo SE is
   reported; a line whose survival is within 2 MC SE of the threshold is
   labelled "marginal".
4. Across fixtures: ticket-watchlist fixtures first, then priced picks by EV,
   then unpriced picks by break-even, then survival, then kick-off. Cap 20;
   fixtures beyond the cap are "qualified, omitted by cap". No minimum.
Rules are frozen before any day's outcomes are inspected.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import POLICY, POLICY_VERSION
from .markets import Settlement, break_even_odds, expected_value, is_integer_line


@dataclass
class LineEval:
    s: Settlement
    qualified: bool
    reason: str
    break_even: float | None
    price: dict | None            # {"price", "bookmaker", "last_update", ...} or None
    ev: float | None
    marginal: bool

    @property
    def key(self):
        return (self.s.market, self.s.line)


def qualifies(s: Settlement) -> tuple[bool, str]:
    if s.survival < POLICY["survival_threshold"]:
        return False, f"survival {s.survival:.1%} < {POLICY['survival_threshold']:.0%}"
    if is_integer_line(s.line) and s.p_win < POLICY["integer_min_win"]:
        return False, f"outright win {s.p_win:.1%} < {POLICY['integer_min_win']:.0%} minimum for a whole-goal line"
    return True, "qualifies"


def evaluate_lines(settlements: list[Settlement], prices: dict | None) -> list[LineEval]:
    out = []
    for s in settlements:
        ok, why = qualifies(s)
        be = break_even_odds(s.p_win, s.p_loss)
        pr = (prices or {}).get((s.market, s.line))
        ev = expected_value(s.p_win, s.p_loss, pr["price"]) if pr else None
        marginal = ok and (s.survival - POLICY["survival_threshold"]) < 2 * s.mc_se
        out.append(LineEval(s, ok, why, be, pr, ev, marginal))
    return out


@dataclass
class FixtureDecision:
    match_id: str
    status: str                       # 'pick' | 'qualified_capped' | 'pass' | 'unrated'
    primary: LineEval | None = None
    lines: list[LineEval] = field(default_factory=list)
    reason: str = ""
    watchlist: bool = False
    rank: int | None = None
    basis: str = ""                   # 'ev' | 'break_even'


def decide_fixture(match_id: str, settlements: list[Settlement], watchlist: bool, prices: dict | None = None) -> FixtureDecision:
    lines = evaluate_lines(settlements, prices)
    q = [l for l in lines if l.qualified]
    if not q:
        best = max(lines, key=lambda l: l.s.survival)
        return FixtureDecision(match_id, "pass", None, lines,
                               f"no line reaches the rule (best: {best.s.market.replace('_over', '')} over {best.s.line:g} at {best.s.survival:.1%} survival)", watchlist)
    priced = [l for l in q if l.ev is not None]
    if priced:
        primary = max(priced, key=lambda l: (l.ev, l.s.survival)); basis = "ev"
    else:
        primary = max(q, key=lambda l: (l.break_even or 0, l.s.survival)); basis = "break_even"
    return FixtureDecision(match_id, "pick", primary, lines, "qualified", watchlist, basis=basis)


def rank_and_cap(decisions: list[FixtureDecision], kickoffs: dict[str, str]) -> list[FixtureDecision]:
    picks = [d for d in decisions if d.status == "pick"]

    def key(d):
        p = d.primary
        return (not d.watchlist, p.ev is None, -(p.ev if p.ev is not None else 0), -(p.break_even or 0), -p.s.survival, kickoffs.get(d.match_id) or "9")
    picks.sort(key=key)
    for i, d in enumerate(picks, start=1):
        d.rank = i
        if i > POLICY["max_picks"]:
            d.status = "qualified_capped"
            d.reason = f"qualified but omitted by the {POLICY['max_picks']}-pick cap (rank {i})"
    return decisions


def policy_description() -> dict:
    return {"version": POLICY_VERSION, **{k: v for k, v in POLICY.items()},
            "primary_rule": "highest EV among qualifying lines when a real price exists; otherwise highest model break-even price among qualifying lines",
            "ranking": "ticket-watchlist first, then priced picks by EV, unpriced by break-even, then survival, then kick-off"}
