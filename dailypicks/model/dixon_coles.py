"""Dixon–Coles (1997) bivariate Poisson-with-dependence goal model.

lambda_home = exp(mu + home_adv + attack_home - defence_away)
lambda_away = exp(mu + attack_away - defence_home)

* One model per competition (league-specific scoring level `mu` and home advantage).
* Exponential time decay w = exp(-xi * days_before_cutoff).
* L2 penalty on attack/defence (shrinks toward league mean; makes the
  attack/defence offsets identifiable). Promoted teams are shrunk toward a
  prior (attack_prior, defence_prior) estimated from training seasons only.
* Dixon–Coles low-score correction tau(x, y) for 0-0, 1-0, 0-1, 1-1.
* Uncertainty: Laplace approximation — the penalised log-likelihood is treated
  as a log-posterior and the covariance is the inverse Hessian at the MAP.
  Assumptions: posterior approximately Gaussian, penalty acts as a prior.

Everything the model consumes comes from `AsOfView`; this module never touches
the database.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

from ..config import MODEL, MODEL_VERSION


@dataclass
class FitResult:
    competition: str
    teams: list[str]
    theta: np.ndarray                 # [attack(T), defence(T), mu, home_adv, rho]
    cov: np.ndarray | None            # Laplace covariance (None if not PD)
    converged: bool
    n_matches: int
    effective_matches: float
    matches_per_team: dict[str, int]
    promoted: set[str]
    cutoff_utc: str
    negloglik: float
    message: str
    model_version: str = MODEL_VERSION
    hyper: dict = field(default_factory=dict)

    @property
    def T(self) -> int:
        return len(self.teams)

    def index(self, team: str) -> int | None:
        try:
            return self.teams.index(team)
        except ValueError:
            return None

    def unpack(self, theta: np.ndarray | None = None):
        th = self.theta if theta is None else theta
        T = self.T
        return th[..., :T], th[..., T:2 * T], th[..., 2 * T], th[..., 2 * T + 1], th[..., 2 * T + 2]

    def rates(self, home: str, away: str, theta: np.ndarray | None = None):
        """(lambda_home, lambda_away, rho) for a fixture, vectorised over leading dims of theta."""
        a, d, mu, h, rho = self.unpack(theta)
        i, j = self.index(home), self.index(away)
        if i is None or j is None:
            raise KeyError("team not in model")
        lam = np.exp(mu + h + a[..., i] - d[..., j])
        mu_a = np.exp(mu + a[..., j] - d[..., i])
        return lam, mu_a, rho


def tau(x, y, lam, mu, rho):
    """Dixon–Coles correction factor; vectorised."""
    x = np.asarray(x); y = np.asarray(y)
    out = np.ones(np.broadcast(x, y, lam, mu, rho).shape)
    out = np.where((x == 0) & (y == 0), 1 - lam * mu * rho, out)
    out = np.where((x == 0) & (y == 1), 1 + lam * rho, out)
    out = np.where((x == 1) & (y == 0), 1 + mu * rho, out)
    out = np.where((x == 1) & (y == 1), 1 - rho, out)
    return out


def _neg_penalised_loglik(theta, hi, ai, x, y, w, T, l2, prior_a, prior_d, prior_w, home_adv_prior_var=1.0):
    a = theta[:T]; d = theta[T:2 * T]; mu = theta[2 * T]; h = theta[2 * T + 1]; rho = theta[2 * T + 2]
    eta_h = mu + h + a[hi] - d[ai]
    eta_a = mu + a[ai] - d[hi]
    lam = np.exp(eta_h); mua = np.exp(eta_a)
    t = tau(x, y, lam, mua, rho)
    if np.any(t <= 0):
        return 1e10, np.zeros_like(theta)
    ll = w * (np.log(t) + x * eta_h - lam + y * eta_a - mua)
    # penalty: (l2 * prior_w_i) * ((a_i - prior_a_i)^2 + (d_i - prior_d_i)^2)
    pen = np.sum(l2 * prior_w * ((a - prior_a) ** 2 + (d - prior_d) ** 2)) + 0.5 * h ** 2 / home_adv_prior_var
    f = -np.sum(ll) + pen
    # gradient
    dl_dlam = w * (x / lam - 1.0)  # wrt lam (Poisson part)
    dl_dmu = w * (y / mua - 1.0)
    # tau derivatives
    z00 = (x == 0) & (y == 0); z01 = (x == 0) & (y == 1); z10 = (x == 1) & (y == 0); z11 = (x == 1) & (y == 1)
    dt_dlam = np.where(z00, -mua * rho, np.where(z01, rho, 0.0))
    dt_dmu = np.where(z00, -lam * rho, np.where(z10, rho, 0.0))
    dt_drho = np.where(z00, -lam * mua, np.where(z01, lam, np.where(z10, mua, np.where(z11, -1.0, 0.0))))
    dl_dlam = dl_dlam + w * dt_dlam / t
    dl_dmu = dl_dmu + w * dt_dmu / t
    g_eta_h = dl_dlam * lam   # d/d eta = d/d lam * lam
    g_eta_a = dl_dmu * mua
    grad = np.zeros_like(theta)
    # attack: eta_h has a[hi], eta_a has a[ai]
    np.add.at(grad, hi, g_eta_h)
    np.add.at(grad, ai, g_eta_a)
    # defence: eta_h has -d[ai], eta_a has -d[hi]
    np.add.at(grad, T + ai, -g_eta_h)
    np.add.at(grad, T + hi, -g_eta_a)
    grad[2 * T] = np.sum(g_eta_h + g_eta_a)
    grad[2 * T + 1] = np.sum(g_eta_h)
    grad[2 * T + 2] = np.sum(w * dt_drho / t)
    grad = -grad
    grad[:T] += 2 * l2 * prior_w * (a - prior_a)
    grad[T:2 * T] += 2 * l2 * prior_w * (d - prior_d)
    grad[2 * T + 1] += h / home_adv_prior_var
    return f, grad


def fit(competition: str, history: list, cutoff_utc: datetime, promoted: set[str] | None = None,
        promoted_prior: tuple[float, float] | None = None, hyper: dict | None = None) -> FitResult:
    """Fit on `history` rows (dicts/Rows with home_id, away_id, home_goals, away_goals, date_local/kickoff_utc)."""
    hp = dict(MODEL)
    if hyper:
        hp.update(hyper)
    promoted = promoted or set()
    cutoff = cutoff_utc.astimezone(timezone.utc)
    rows = [r for r in history if r["home_goals"] is not None]
    teams = sorted({r["home_id"] for r in rows} | {r["away_id"] for r in rows})
    T = len(teams)
    idx = {t: k for k, t in enumerate(teams)}
    hi = np.array([idx[r["home_id"]] for r in rows]); ai = np.array([idx[r["away_id"]] for r in rows])
    x = np.array([r["home_goals"] for r in rows], dtype=float); y = np.array([r["away_goals"] for r in rows], dtype=float)
    days = np.array([(cutoff - _match_dt(r)).total_seconds() / 86400.0 for r in rows])
    if np.any(days < 0):
        raise ValueError("history contains matches after the cutoff (leakage guard)")
    w = np.exp(-hp["xi"] * days)
    prior_a = np.zeros(T); prior_d = np.zeros(T); prior_w = np.ones(T)
    if promoted_prior is not None:
        for t in promoted:
            if t in idx:
                prior_a[idx[t]] = promoted_prior[0]; prior_d[idx[t]] = promoted_prior[1]
                prior_w[idx[t]] = hp["l2_promoted_scale"]
    theta0 = np.zeros(2 * T + 3)
    theta0[2 * T] = math.log(max(1e-3, np.average((x + y) / 2, weights=w)))
    theta0[2 * T + 1] = 0.2
    theta0[2 * T + 2] = -0.05
    args = (hi, ai, x, y, w, T, hp["l2"], prior_a, prior_d, prior_w)
    bounds = [(None, None)] * (2 * T + 2) + [hp["rho_bounds"]]
    res = minimize(_neg_penalised_loglik, theta0, args=args, jac=True, method="L-BFGS-B", bounds=bounds,
                   options={"maxiter": 2000, "ftol": 1e-12, "gtol": 1e-7})
    theta = res.x
    cov = None
    converged = bool(res.success) and np.all(np.isfinite(theta))
    msg = res.message if isinstance(res.message, str) else str(res.message)
    if converged:
        H = _numerical_hessian(lambda th: _neg_penalised_loglik(th, *args)[1], theta)
        H = 0.5 * (H + H.T)
        try:
            evals = np.linalg.eigvalsh(H)
            if evals.min() > 1e-8:
                cov = np.linalg.inv(H)
            else:
                msg += f"; Hessian not positive definite (min eig {evals.min():.2e})"
                converged = False
        except np.linalg.LinAlgError as e:
            msg += f"; Hessian failure {e}"
            converged = False
    counts: dict[str, int] = {t: 0 for t in teams}
    for r in rows:
        counts[r["home_id"]] += 1; counts[r["away_id"]] += 1
    return FitResult(competition, teams, theta, cov, converged, len(rows), float(w.sum()), counts,
                     set(promoted), cutoff.isoformat(), float(res.fun), msg, hyper=hp)


def _match_dt(r) -> datetime:
    if r["kickoff_utc"]:
        return datetime.fromisoformat(r["kickoff_utc"])
    return datetime.fromisoformat(r["date_local"] + "T12:00:00+00:00")


def _numerical_hessian(grad_fn, theta: np.ndarray, eps: float = 1e-5) -> np.ndarray:
    n = len(theta)
    H = np.zeros((n, n))
    for k in range(n):
        tp = theta.copy(); tp[k] += eps
        tm = theta.copy(); tm[k] -= eps
        H[k] = (grad_fn(tp) - grad_fn(tm)) / (2 * eps)
    return H


# ---------------------------------------------------------------- score distribution

def score_matrix(lam: float, mu: float, rho: float, max_goals: int | None = None) -> tuple[np.ndarray, float]:
    """Joint P(home=x, away=y) on a (max_goals+1)^2 grid and the tail mass outside the grid.
    The matrix is NOT renormalised; callers must check the tail mass against the tolerance."""
    G = (max_goals or MODEL["max_goals"]) + 1
    k = np.arange(G)
    px = np.exp(k * math.log(lam) - lam - gammaln(k + 1))
    py = np.exp(k * math.log(mu) - mu - gammaln(k + 1))
    P = np.outer(px, py)
    P[0, 0] *= 1 - lam * mu * rho; P[0, 1] *= 1 + lam * rho; P[1, 0] *= 1 + mu * rho; P[1, 1] *= 1 - rho
    tail = 1.0 - P.sum()
    return P, tail


def score_matrices(lam: np.ndarray, mu: np.ndarray, rho: np.ndarray, max_goals: int | None = None):
    """Vectorised joint score grids for K parameter draws: returns (K, G, G) and tail (K,)."""
    G = (max_goals or MODEL["max_goals"]) + 1
    k = np.arange(G)
    lg = gammaln(k + 1)
    px = np.exp(k[None, :] * np.log(lam)[:, None] - lam[:, None] - lg[None, :])
    py = np.exp(k[None, :] * np.log(mu)[:, None] - mu[:, None] - lg[None, :])
    P = px[:, :, None] * py[:, None, :]
    P[:, 0, 0] *= 1 - lam * mu * rho; P[:, 0, 1] *= 1 + lam * rho; P[:, 1, 0] *= 1 + mu * rho; P[:, 1, 1] *= 1 - rho
    tail = 1.0 - P.sum(axis=(1, 2))
    return P, tail


def valid_distribution(P: np.ndarray, tail: float, tol: float | None = None) -> tuple[bool, str]:
    tol = MODEL["tail_tolerance"] if tol is None else tol
    if not np.all(np.isfinite(P)):
        return False, "non-finite probabilities"
    if np.any(P < 0):
        return False, "negative probability (Dixon–Coles tau out of range)"
    if tail > tol:
        return False, f"tail mass {tail:.2e} exceeds tolerance {tol:.0e}"
    if abs(P.sum() + tail - 1.0) > 1e-9:
        return False, "distribution does not sum to one"
    return True, "ok"
