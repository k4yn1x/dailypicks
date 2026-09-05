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

MARKETS: list[tuple[str, float]] = [
    ("match_over", 0.5), ("match_over", 1.0), ("match_over", 1.5), ("match_over", 2.0), ("match_over", 2.5),
    ("home_over", 0.5), ("home_over", 1.5), ("away_over", 0.5), ("away_over", 1.5),
]


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
    if market == "match_over":
        return f"Match Over {line:g} goals"
    team = home_name if market == "home_over" else away_name
    return f"{team} Over {line:g} goals"
