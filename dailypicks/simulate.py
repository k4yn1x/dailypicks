"""Posterior-predictive match simulation.

For a fitted competition model and one fixture:
1. Draw K parameter vectors theta_k ~ N(theta_hat, Sigma) (Laplace approximation).
2. For each draw compute (lambda_k, mu_k, rho_k) and the joint Dixon–Coles score
   grid. A draw is VALID only if every grid cell is finite and non-negative and
   the mass outside the grid is within the tail tolerance. Invalid draws are
   discarded and counted — never clipped or renormalised.
3. Sample exactly one (home, away) score pair from each valid grid.
4. Continue until >= n valid pairs exist (bounded rounds), then truncate to n.

The output is n coherent simulated match outcomes under parameter uncertainty.
They are NOT n independently trained models. The random seed is deterministic
per fixture and recorded.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from .config import MODEL, SIM
from .model.dixon_coles import FitResult, score_matrices


@dataclass
class SimResult:
    match_id: str
    seed: int
    requested: int
    valid: int
    drawn: int
    invalid_tail: int
    invalid_negative: int
    invalid_nonfinite: int
    home: np.ndarray
    away: np.ndarray
    diagnostics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.valid >= self.requested


def deterministic_seed(match_id: str, cutoff_iso: str, model_version: str) -> int:
    h = hashlib.sha256(f"{match_id}|{cutoff_iso}|{model_version}".encode()).digest()
    return int.from_bytes(h[:4], "big")


def simulate_fixture(fit: FitResult, match_id: str, home: str, away: str, seed: int | None = None,
                     n: int | None = None) -> SimResult:
    n = n or SIM["n"]
    if fit.cov is None:
        raise ValueError("fit has no covariance; cannot sample parameters")
    seed = deterministic_seed(match_id, fit.cutoff_utc, fit.model_version) if seed is None else seed
    rng = np.random.default_rng(seed)
    G = MODEL["max_goals"] + 1
    tol = MODEL["tail_tolerance"]
    hs, as_ = [], []
    drawn = valid = bad_tail = bad_neg = bad_nan = 0
    lam_all, mu_all, rho_all = [], [], []
    L = np.linalg.cholesky(fit.cov + 1e-12 * np.eye(len(fit.theta)))
    rounds = 0
    while valid < n and rounds < SIM["max_rounds"]:
        rounds += 1
        k = int((n - valid) * SIM["oversample"]) + 16
        z = rng.standard_normal((k, len(fit.theta)))
        theta = fit.theta[None, :] + z @ L.T
        lam, mu, rho = fit.rates(home, away, theta)
        P, tail = score_matrices(lam, mu, rho)
        finite = np.isfinite(P).all(axis=(1, 2)) & np.isfinite(tail)
        nonneg = np.where(finite, (P >= 0).all(axis=(1, 2)), False)
        tail_ok = np.where(finite, (tail <= tol) & (tail >= -1e-9), False)
        good = finite & nonneg & tail_ok
        drawn += k
        bad_nan += int((~finite).sum()); bad_neg += int((finite & ~nonneg).sum()); bad_tail += int((finite & nonneg & ~tail_ok).sum())
        Pg = P[good].reshape(-1, G * G)
        if Pg.shape[0] == 0:
            continue
        cum = np.cumsum(Pg, axis=1)
        u = rng.random(Pg.shape[0]) * cum[:, -1]   # scale by realised mass (<= 1 by tolerance) to stay in-grid
        idx = (cum < u[:, None]).sum(axis=1)
        idx = np.minimum(idx, G * G - 1)
        hs.append(idx // G); as_.append(idx % G)
        lam_all.append(lam[good]); mu_all.append(mu[good]); rho_all.append(rho[good])
        valid += Pg.shape[0]
    home_g = np.concatenate(hs)[:n] if hs else np.array([], dtype=int)
    away_g = np.concatenate(as_)[:n] if as_ else np.array([], dtype=int)
    lam_v = np.concatenate(lam_all)[:n] if lam_all else np.array([])
    mu_v = np.concatenate(mu_all)[:n] if mu_all else np.array([])
    rho_v = np.concatenate(rho_all)[:n] if rho_all else np.array([])
    diag = {}
    if len(home_g):
        tot = home_g + away_g
        diag = {
            "lambda_home_mean": float(lam_v.mean()), "lambda_home_sd": float(lam_v.std()),
            "lambda_away_mean": float(mu_v.mean()), "lambda_away_sd": float(mu_v.std()),
            "rho_mean": float(rho_v.mean()),
            "sim_mean_total": float(tot.mean()), "sim_sd_total": float(tot.std()),
            "sim_p_0_0": float(np.mean((home_g == 0) & (away_g == 0))),
            "sim_max_total": int(tot.max()),
            "rounds": rounds,
        }
    return SimResult(match_id, seed, n, min(valid, n), drawn, bad_tail, bad_neg, bad_nan, home_g, away_g, diag)
