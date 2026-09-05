# Operations

## What runs where

| Piece | Where | Notes |
|---|---|---|
| Website | Claude artifact **DailyPicks Goal Board** (private until shared) | Static single file; data embedded at build time. Opening it never runs the model. |
| Daily refresh | Claude scheduled task **DailyPicks daily refresh (05:30 Chicago)** — cloud session, runs with your computer off | Two UTC slots (10:30 and 11:30) with a local-time gate so exactly one run happens at 05:30 America/Chicago across DST. |
| State between runs | Claude artifact **DailyPicks Ops Bundle** | Source tree + immutable ledger + priors + closing-odds extracts + frozen evaluation + last board, packed as a tar.gz inside the page. The refresh restores it, runs, and republishes it. |
| Optional upgrade | GitHub Actions (`.github/workflows/daily.yml`, `scripts/daily.sh`) | Deterministic cron with full internet access (ESPN cross-check with kick-off times, football-data odds refresh) and GitHub Pages hosting. Needs a repo + token. |

## Daily run, step by step

1. Gate on local time (05:xx America/Chicago) unless forced.
2. Restore the ops bundle → `python -m dailypicks.pipeline --trigger scheduled-0530-chicago`
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
python -m pytest -q                            # 47 tests
python scripts/make_bundle.py bundle.html      # pack state
python scripts/make_bundle.py --restore bundle.html /path/to/dir
```

Changing any selection rule requires bumping `POLICY_VERSION`; changing model settings requires bumping `MODEL_VERSION` and re-running the validation/test protocol.
