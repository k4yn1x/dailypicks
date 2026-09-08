# Operations

## What runs where

| Piece | Where | Notes |
|---|---|---|
| Website | Claude artifact **DailyPicks Goal Board** (private until shared) | Static single file; data embedded at build time. Opening it never runs the model. |
| Daily refresh | Claude scheduled task **DailyPicks daily refresh (07:00 WAT)** — cloud session, runs with your computer off | Single 06:00 UTC schedule = 07:00 Africa/Lagos (WAT has no daylight saving); the task still checks the local hour is 07 before running. |
| State between runs | Claude artifact **DailyPicks Ops Bundle** | Source tree + immutable ledger + priors + closing-odds extracts + frozen evaluation + last board, packed as a tar.gz inside the page. The refresh restores it, runs, and republishes it. |
| Optional upgrade | GitHub Actions (`.github/workflows/daily.yml`, `scripts/daily.sh`) | Deterministic cron with full internet access (ESPN cross-check with kick-off times, football-data odds refresh) and GitHub Pages hosting. Needs a repo + token. |

## Daily run, step by step

1. Gate on local time (07:xx Africa/Lagos) unless forced.
2. Restore the ops bundle → `python -m dailypicks.pipeline --trigger scheduled-0700-wat`
   - lock file → ingest (git fetch openfootball, archive with hash, validate, atomic dataset swap)
   - settle open ledger rows that now have results
   - fit 8 league models → rate fixtures (gates: kick-off known and > now+15 min, ≥3 matches per team, converged fit, ≥10,000 valid simulations, valid distribution)
   - independent cross-check (ESPN): postponed / in-progress / not-found fixtures are not recommended
   - frozen selection policy → board per display day (today final, later days provisional)
   - record every rated market in the immutable ledger; validate the candidate board; atomic write of `data/published/latest.json`, dated copy, `status.json`
3. Build the site, republish the board artifact, republish the ops bundle.
4. Failure: the previous board stays live; `status.json` records `status=failed` + error; the run row in `runs` is `failed`; the scheduled task ends with a failure summary (push notification enabled). Success is quiet.

Run log stages (table `runs`, column `stages_json`): scheduled → started → data_fetched → model_fitted → simulations_completed → validated → published | failed.

## Staleness

The page compares its `generated_at_utc` with the viewer's clock and shows a **Stale** pill after 30 h. Fixtures whose kick-off has passed are greyed out client-side as "Started" between refreshes.

## Selection policy select-2.0.0 (odds-aware, team + match totals)

Per fixture, the same 10,000 simulated scores settle 15 lines: home-team goals, away-team goals and total match goals, each at Over 0.5 / 1.0 / 1.5 / 2.0 / 2.5. Labels always name whose goals a line concerns. A line qualifies at ≥80% unrounded survival (win + push for whole-goal lines) and ≥60% outright win for whole-goal lines. Every line shows win / push / loss, a model break-even price (1 + P(loss)/P(win), labelled as an estimate) and, when a real bookmaker price exists, the price with bookmaker + timestamp and EV = P(win)(d−1) − P(loss). Primary = highest EV among qualifying priced lines; without prices, highest break-even among qualifying lines (never the lowest line by default, never a non-qualifying line to raise odds). Prices older than 24 h are shown as stale and not used.

**Odds source:** `dailypicks/sources/oddsapi.py` (The Odds API v4, `ODDS_API_KEY` env var, markets totals / alternate_totals / team_totals / alternate_team_totals). Not configured in the current deployment and, like other non-GitHub hosts, unlikely to be reachable from the cloud refresh session — so the live board shows break-even estimates only. It is untested against the live API (no key); on GitHub Actions add the key as a repository secret.

## Known limitations (honest list)

- Only eight competitions have a live source (openfootball). 58 of the 110 ticket teams are in leagues with no ingested source; they are listed as *awaiting coverage*, never silently dropped.
- openfootball publishes kick-off times only a few matchdays ahead; date-only fixtures are shown as **Unrated** ("kickoff time not yet published") and pick up a time on a later refresh. Its dates for later rounds can differ from the official schedule; the ESPN cross-check flags those.
- The cloud refresh cannot reach ESPN directly; it verifies team pairings and match status via a fetch tool that does not return reliable kick-off times, so the daily cross-check confirms *who plays and whether the match is on*, not the minute. The GitHub Actions path verifies times too.
- No bookmaker odds in the daily run → no EV, no CLV, no ROI. Closing-odds extracts exist only for the evaluation seasons.
- The model does **not** beat the Pinnacle closing line on Over 2.5 in the test seasons (see docs/EVALUATION.md). The site says so.

## Manual operations

```
python -m dailypicks.pipeline --offline        # rebuild the board from the already-cloned data (no network)
python scripts/build_site.py                   # rebuild site/dist
python -m pytest -q                            # 51 tests
python scripts/make_bundle.py bundle.html      # pack state
python scripts/make_bundle.py --restore bundle.html /path/to/dir
```

Changing any selection rule requires bumping `POLICY_VERSION`; changing model settings requires bumping `MODEL_VERSION` and re-running the validation/test protocol.

## Verification status of the hosted refresh (2026-09-05)

- Pipeline, tests (47), site build and artifact publish were run and verified from the build session (board run_id 20260905T161803Z is live).
- One scheduled task exists and is enabled (06:00 UTC = 07:00 WAT; the Chicago winter slot was deleted on 2026-09-06). A manual "FORCE" fire of the 10:30 task ended in 26 s without republishing (it evidently took the time-gate branch). A one-off verification task without the gate was fired at 16:31 UTC and was still running 50 minutes later when the build session ended; its result was NOT confirmed. Treat the first unattended 07:00 WAT run (2026-09-07 06:00 UTC) as the acceptance test: it sends a push notification on completion, and the board's "Refreshed" pill shows the run time. If it does not refresh, the previous board stays live and shows a Stale warning after 30 h.
- Recommended hardening: move the refresh to GitHub Actions (`.github/workflows/daily.yml`), which removes the LLM from the daily loop.

## Acceptance result (2026-09-08)

- The first fully unattended 07:00 WAT refresh ran on 2026-09-08 (fired 06:25 UTC, SUCCEEDED, 2m41s). The live board carries run_id 20260908T062632Z, policy select-2.0.0, 7/7 fixtures rated, cross-check partial (13 league-dates, 12 verified, 1 mismatch; later dates unavailable via proxy), odds not configured. The ops bundle was republished the same day.
