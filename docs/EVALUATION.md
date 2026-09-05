# Evaluation protocol and frozen settings

## Split (defined before any tuning)

| Split      | Seasons (all eight competitions where the source has them) |
|------------|-------------------------------------------------------------|
| train      | 2012-13 … 2021-22 (history for fitting; promoted-team priors) |
| validation | 2022-23, 2023-24 (hyper-parameter grid only)                 |
| test       | 2024-25, 2025-26 (untouched until settings were frozen)      |

Walk-forward: every match date is a cutoff at 00:00 UTC that morning; the model
is refitted from the guarded as-of view (results visible only 3 h after kick-off
and before the cutoff; promoted status from schedule membership; priors from
training seasons). Posterior-predictive market probabilities average the score
grid over 300 (tuning) / 500 (final) parameter draws from the Laplace
approximation — the same mechanism as the live 10,000-draw simulation.

Exclusions are recorded per league-season: teams with fewer than 3 matches in
the competition within the 3-year window, non-converged fits, invalid draws.

## Grid on validation (avg log loss over the 7 win-events used by the board)

| xi (per day) | l2 | avg log loss | avg calibration slope |
|---|---|---|---|
| 0.001 | 1 | 0.54530 | 0.84 |
| 0.001 | 2 | 0.54478 | 0.87 |
| 0.001 | 4 | 0.54437 | 0.92 |
| 0.002 | 1 | 0.54465 | 0.84 |
| 0.002 | 2 | 0.54405 | 0.88 |
| **0.002** | **4** | **0.54364** | **0.95** |
| 0.002 | 8 | 0.54372 | 1.04 |
| 0.004 | 1 | 0.54544 | 0.80 |
| 0.004 | 2 | 0.54484 | 0.86 |
| 0.004 | 4 | 0.54404 | 0.94 |

Reference (league-rate Poisson, same decay, no team strengths): 0.5641.
Frozen: xi = 0.002, l2 = 4.0, l2_promoted_scale = 3, history window 3 years,
min 3 matches per team, score grid 0–20, tail tolerance 1e-4. These values are
in `dailypicks/config.py` and were frozen before `evaluate test` was run.

## Market baseline rule

Pinnacle closing Over/Under 2.5 (football-data.co.uk `PC>2.5` / `PC<2.5`) is
the only timestamped two-sided price available. Rule, recorded before running
the test evaluation: a league-season enters the market comparison only if
≥ 90 % of its matches with results have a valid paired Pinnacle closing price;
otherwise the market comparison for that league-season is **stopped** and
reported as such. Max/Avg coverage is reported separately and never
substituted. De-vig methods: proportional, power, Shin (all verified to sum to
one and to leave a fair book unchanged; they differ for unbalanced books).
Model and market are scored on exactly the same fixtures.

Observed coverage (paired Pinnacle closing / matches): 2024-25 ≥ 96 % in every
league (evaluated); 2025-26 between 39 % and 55 % in every league (stopped —
the source only carries closing prices for part of that season).

## Test results

See `data/eval/test_frozen.json` (published on the site's Results tab). The
test split was run once with the frozen settings; it is not used for tuning.
