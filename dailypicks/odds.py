"""Odds utilities: vig removal and value arithmetic.

Implied probabilities pi_i = 1/o_i overround by sum(pi) - 1. Three de-vig methods:
* proportional: p_i = pi_i / sum(pi)
* power:        p_i = pi_i ** k with k chosen so that sum(p) = 1
* Shin (1993):  p_i = (sqrt(z^2 + 4(1-z) pi_i^2 / S) - z) / (2(1-z)), S = sum(pi),
                z (insider fraction) chosen so that sum(p) = 1
For a fair book (sum(pi) = 1) all three return pi unchanged. For two-outcome
markets with equal overround on both sides, proportional and power differ only
slightly; the methods are kept separate and verified in tests rather than
assumed to differ.
"""
from __future__ import annotations

import math


def implied(odds: list[float]) -> list[float]:
    return [1.0 / o for o in odds]


def devig_proportional(odds: list[float]) -> list[float]:
    pi = implied(odds); s = sum(pi)
    return [p / s for p in pi]


def devig_power(odds: list[float], tol: float = 1e-12) -> list[float]:
    pi = implied(odds)
    lo, hi = 0.5, 3.0
    for _ in range(200):
        k = 0.5 * (lo + hi)
        s = sum(p ** k for p in pi)
        if abs(s - 1) < tol:
            break
        if s > 1:
            lo = k
        else:
            hi = k
    return [p ** k for p in pi]


def devig_shin(odds: list[float], tol: float = 1e-12) -> list[float]:
    pi = implied(odds); S = sum(pi)
    if S <= 1:
        return [p / S for p in pi]

    def probs(z):
        return [(math.sqrt(z * z + 4 * (1 - z) * p * p / S) - z) / (2 * (1 - z)) for p in pi]
    lo, hi = 0.0, 0.5
    for _ in range(200):
        z = 0.5 * (lo + hi)
        s = sum(probs(z))
        if abs(s - 1) < tol:
            break
        if s > 1:
            lo = z
        else:
            hi = z
    return probs(z)


def expected_value(p_win: float, p_loss: float, decimal_odds: float) -> float:
    """Expected profit per unit stake; pushes return the stake (contribute 0)."""
    return p_win * (decimal_odds - 1) - p_loss


def clv_raw(taken_odds: float, closing_odds: float) -> float:
    """Raw closing-line value for a no-push market: positive when the taken price was higher than the close."""
    return taken_odds / closing_odds - 1
