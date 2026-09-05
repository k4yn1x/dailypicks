"""Daily selection policy (POLICY_VERSION in config).

Per fixture (already simulated, all data-quality gates passed):
  1. A market QUALIFIES when unrounded survival >= 0.80 AND, for integer
     lines, unrounded outright win >= 0.60 (documented minimum so that
     push-heavy lines cannot dominate the card).
  2. The PRIMARY line is the first qualifying market in POLICY["primary_order"]
     (highest match-total line first, then team lines). Exactly one primary per
     fixture; other qualifying lines are reported as secondary (never as picks).
Across fixtures:
  3. Rank: ticket-watchlist fixtures first, then line level (2.5 > 2.0 > ...
     > team lines), then survival (desc), then kickoff time.
  4. Cap at POLICY["max_picks"]. Fixtures beyond the cap are recorded as
     "qualified, omitted by cap" — not as model failures.
There is no minimum: if six fixtures qualify, six are shown.
These rules were frozen before any day's outcomes were inspected.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import POLICY, POLICY_VERSION
from .markets import Settlement, is_integer_line

LINE_LEVEL = {("match_over", 2.5): 9, ("match_over", 2.0): 8, ("match_over", 1.5): 7, ("match_over", 1.0): 6,
              ("match_over", 0.5): 5, ("home_over", 1.5): 4, ("away_over", 1.5): 4, ("home_over", 0.5): 3, ("away_over", 0.5): 3}


def qualifies(s: Settlement) -> tuple[bool, str]:
    if s.survival < POLICY["survival_threshold"]:
        return False, f"survival {s.survival:.3f} < {POLICY['survival_threshold']:.2f}"
    if is_integer_line(s.line) and s.p_win < POLICY["integer_min_win"]:
        return False, f"outright win {s.p_win:.3f} < {POLICY['integer_min_win']:.2f} (integer line)"
    return True, "ok"


@dataclass
class FixtureDecision:
    match_id: str
    status: str                       # 'pick' | 'qualified_capped' | 'pass' | 'unrated'
    primary: Settlement | None = None
    secondary: list[Settlement] = field(default_factory=list)
    reason: str = ""
    watchlist: bool = False
    rank: int | None = None
    market_reasons: dict = field(default_factory=dict)


def decide_fixture(match_id: str, settlements: list[Settlement], watchlist: bool) -> FixtureDecision:
    by_key = {(s.market, s.line): s for s in settlements}
    reasons = {}
    qualified = []
    for key in POLICY["primary_order"]:
        s = by_key.get(key)
        if s is None:
            continue
        ok, why = qualifies(s)
        reasons[f"{key[0]}:{key[1]}"] = why
        if ok:
            qualified.append(s)
    if not qualified:
        best = max(settlements, key=lambda s: s.survival)
        return FixtureDecision(match_id, "pass", None, [], f"no market reaches the rule (best: {best.market} {best.line:g} survival {best.survival:.1%})",
                               watchlist, market_reasons=reasons)
    primary, secondary = qualified[0], qualified[1:]
    return FixtureDecision(match_id, "pick", primary, secondary, "qualified", watchlist, market_reasons=reasons)


def rank_and_cap(decisions: list[FixtureDecision], kickoffs: dict[str, str]) -> list[FixtureDecision]:
    picks = [d for d in decisions if d.status == "pick"]
    picks.sort(key=lambda d: (not d.watchlist, -LINE_LEVEL[(d.primary.market, d.primary.line)], -d.primary.survival, kickoffs.get(d.match_id) or "9"))
    for i, d in enumerate(picks, start=1):
        d.rank = i
        if i > POLICY["max_picks"]:
            d.status = "qualified_capped"
            d.reason = f"qualified but omitted by the {POLICY['max_picks']}-pick cap (rank {i})"
    return decisions


def policy_description() -> dict:
    return {"version": POLICY_VERSION, **{k: v for k, v in POLICY.items() if k != "primary_order"},
            "primary_order": [f"{m}:{l}" for m, l in POLICY["primary_order"]]}
