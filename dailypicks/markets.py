"""Goal-market definitions and coherent settlement from simulated score pairs.

Survival definition (frozen with POLICY_VERSION):
* half-goal lines (0.5, 1.5, 2.5): survival == win
* integer Asian lines (1.0, 2.0):  survival == win + push
  Over 1.0: win at 2+ goals, push at exactly 1, lose at 0.
  Over 2.0: win at 3+ goals, push at exactly 2, lose at 0-1.
* Team Over 0.5 / 1.5: selected team's goals only.

All markets for a fixture are settled from the SAME array of (home, away)
score pairs, so outcomes are mutually coherent.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

LINES = [0.5, 1.0, 1.5, 2.0, 2.5]
MARKETS: list[tuple[str, float]] = [(m, l) for m in ("match_over", "home_over", "away_over") for l in LINES]


def is_integer_line(line: float) -> bool:
    return float(line).is_integer()


@dataclass(frozen=True)
class Settlement:
    market: str
    line: float
    p_win: float
    p_push: float
    p_loss: float
    n: int

    @property
    def survival(self) -> float:
        return self.p_win + (self.p_push if is_integer_line(self.line) else 0.0)

    @property
    def mc_se(self) -> float:
        """Monte Carlo standard error of the survival estimate."""
        s = self.survival
        return math.sqrt(max(s * (1 - s), 0.0) / self.n)


def relevant_goals(market: str, home: np.ndarray, away: np.ndarray) -> np.ndarray:
    if market == "match_over":
        return home + away
    if market == "home_over":
        return home
    if market == "away_over":
        return away
    raise ValueError(market)


def settle_one(market: str, line: float, home_goals: int, away_goals: int) -> str:
    """Settle a single completed match: 'win' | 'push' | 'loss'."""
    g = relevant_goals(market, np.array([home_goals]), np.array([away_goals]))[0]
    if g > line:
        return "win"
    if is_integer_line(line) and g == line:
        return "push"
    return "loss"


def settle_all(home: np.ndarray, away: np.ndarray, markets: list[tuple[str, float]] | None = None) -> list[Settlement]:
    markets = markets or MARKETS
    n = len(home)
    out = []
    for market, line in markets:
        g = relevant_goals(market, home, away)
        win = int(np.count_nonzero(g > line))
        push = int(np.count_nonzero(g == line)) if is_integer_line(line) else 0
        loss = n - win - push
        out.append(Settlement(market, line, win / n, push / n, loss / n, n))
    return out


def describe(market: str, line: float, home_name: str, away_name: str) -> str:
    """Unambiguous label: always says whose goals the line concerns."""
    if market == "match_over":
        return f"Total match goals Over {line:.1f}"
    team = home_name if market == "home_over" else away_name
    return f"{team} Over {line:.1f} team goals"


def subject(market: str, home_name: str, away_name: str) -> str:
    return "total match goals" if market == "match_over" else (home_name if market == "home_over" else away_name) + " goals"


def settlement_text(market: str, line: float, home_name: str, away_name: str) -> list[str]:
    """Plain-language settlement rules, e.g. Chelsea Over 1.0: 2+ to win, exactly 1 refund, 0 lose."""
    who = "The match needs" if market == "match_over" else f"{home_name if market == 'home_over' else away_name} must score"
    g = lambda n: f"{n} goal{'' if n == 1 else 's'}"
    if is_integer_line(line):
        n = int(line)
        lose = "0 goals to lose." if n == 1 else f"{n - 1} or fewer goals to lose."
        return [f"{who} {n + 1}+ goals to win.", f"Exactly {g(n)} for a refund (push).", lose]
    n = int(math.ceil(line))
    lose = "0 goals to lose." if n == 1 else f"{n - 1} or fewer goals to lose."
    return [f"{who} {n}+ goals to win.", lose]


def break_even_odds(p_win: float, p_loss: float) -> float | None:
    """Decimal odds at which EV = 0 for a market with pushes returning the stake: 1 + p_loss / p_win."""
    return 1 + p_loss / p_win if p_win > 0 else None


def expected_value(p_win: float, p_loss: float, decimal_odds: float) -> float:
    return p_win * (decimal_odds - 1) - p_loss
