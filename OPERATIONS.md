# Operations

## What runs where

| Piece | Where | Notes |
|---|---|---|
| Website | **GitHub Pages** — https://k4yn1x.github.io/dailypicks/ (repo `k4yn1x/dailypicks`) | Static single file built by the workflow; `latest.json` is published next to it. Opening it never runs the model. Always shows the latest run — no version pinning. |
| Daily refresh | **GitHub Actions** `.github/workflows/daily.yml` → `scripts/daily.sh` | Cron 06:00 UTC (= 07:00 Africa/Lagos, no DST) for the full run; 15:00 UTC status refresh; `workflow_dispatch` with `force=true` for manual runs. Runs on GitHub's servers — nothing on your computer needs to be on. |
| State between runs | The repo itself | The workflow commits `data/ledger.sqlite`, `data/published/`, priors and watchlist after every run (`[skip ci]`). The ledger's immutability trigger still applies. |
| Retired | Claude artifact board + ops bundle + scheduled task (2026-09-05 → 2026-09-08) | Frozen at run 20260908T062632Z. Public artifact links pin a fixed version, which is why hosting moved. |

## Daily run, step by step

1. Gate on local time (07:xx Africa/Lagos) unless forced.
2. `scripts/daily.sh` → `python -m dailypicks.pipeline --trigger scheduled-0700-wat`
   - lock file → ingest (git fetch openfootball, archive with hash, validate, atomic dataset swap)
   - settle open ledger rows that now have results
   - fit 8 league models → rate fixtures (gates: kick-off known and > now+15 min, ≥3 matches per team, converged fit, ≥10,000 valid simulations, valid distribution)
   - independent cross-check (ESPN): postponed / in-progress / not-found fixtures are not recommended
   - frozen selection policy → board per display day (today final, later days provisional)
   - record every rated market in the immutable ledger; validate the candidate board; atomic write of `data/published/latest.json`, dated copy, `status.json`
3. Build the site (`site/dist/pages/`), commit ledger + board to `main`, deploy to GitHub Pages.
4. Failure: `daily.sh` exits 1 → the workflow fails (GitHub emails the repo owner), nothing is deployed, the previous Pages build stays live; `status.json` records `status=failed` + error; the run row in `runs` is `failed`. Success is quiet.

Run log stages (table `runs`, column `stages_json`): scheduled → started → data_fetched → model_fitted → simulations_completed → validated → published | failed.

## Staleness

The page compares its `generated_at_utc` with the viewer's clock and shows a **Stale** pill after 30 h. Fixtures whose kick-off has passed are greyed out client-side as "Started" between refreshes.

## Selection policy select-2.0.0 (odds-aware, team + match totals)

Per fixture, the same 10,000 simulated scores settle 15 lines: home-team goals, away-team goals and total match goals, each at Over 0.5 / 1.0 / 1.5 / 2.0 / 2.5. Labels always name whose goals a line concerns. A line qualifies at ≥80% unrounded survival (win + push for whole-goal lines) and ≥60% outright win for whole-goal lines. Every line shows win / push / loss, a model break-even price (1 + P(loss)/P(win), labelled as an estimate) and, when a real bookmaker price exists, the price with bookmaker + timestamp and EV = P(win)(d−1) − P(loss). Primary = highest EV among qualifying priced lines; without prices, highest break-even among qualifying lines (never the lowest line by default, never a non-qualifying line to raise odds). Prices older than 24 h are shown as stale and not used.

**Odds source:** `dailypicks/sources/oddsapi.py` (The Odds API v4, `ODDS_API_KEY` env var, markets totals / alternate_totals / team_totals / alternate_team_totals). Not configured in the current deployment and, like other non-GitHub hosts, unlikely to be reachable from the cloud refresh session — so the live board shows break-even estimates only. It is untested against the live API (no key); on GitHub Actions add the key as a repository secret.

## Known limitations (honest list)

- Only eight competitions have a live source (openfootball). 58 of the 110 ticket teams are in leagues with no ingested source; they are listed as *awaiting coverage*, never silently dropped.
- openfootball publishes kick-off times only a few matchdays ahead; date-only fixtures are shown as **Unrated** ("kickoff time not yet published") and pick up a time on a later refresh. Its dates for later rounds can differ from the official schedule; the ESPN cross-check flags those.
- ESPN's `site.api.espn.com` host returns 403 to GitHub-hosted runners; the cross-check uses `site.web.api.espn.com` (same JSON) and verifies pairings, status **and** kick-off times. Fixtures that mismatch or are not found are never recommended.
- No bookmaker odds in the daily run → no EV, no CLV, no ROI. Closing-odds extracts exist only for the evaluation seasons. To enable live prices add an `ODDS_API_KEY` repository secret (The Odds API); the workflow already passes it through.
- The model does **not** beat the Pinnacle closing line on Over 2.5 in the test seasons (see docs/EVALUATION.md). The site says so.

## Manual run / where to look

- Actions tab → **Daily refresh** → *Run workflow* (force=true) for an on-demand refresh.
- Each run's `latest.json`: https://k4yn1x.github.io/dailypicks/latest.json (`run_id`, `cross_check`, per-fixture `verification`).
- Runs also appear in the `runs` table of `data/ledger.sqlite` in the repo.

## Manual operations

```
python -m dailypicks.pipeline --offline        # rebuild the board from the already-cloned data (no network)
python scripts/build_site.py                   # rebuild site/dist
python -m pytest -q                            # 51 tests
python scripts/make_bundle.py bundle.html      # pack state
python scripts/make_bundle.py --restore bundle.html /path/to/dir
```

Changing any selection rule requires bumping `POLICY_VERSION`; changing model settings requires bumping `MODEL_VERSION` and re-running the validation/test protocol.

## Verification status (2026-09-08)

- Claude-hosted phase: the 07:00 WAT scheduled task ran unattended on 2026-09-08 (run 20260908T062632Z) and republished the artifact board + bundle. Cross-check there was pairing-only and partial (proxy errors on later dates).
- GitHub phase: repo `k4yn1x/dailypicks` created and pushed 2026-09-08; workflow runs 34221661865 (first deploy), 34222101176 and 34222486648 (ESPN host fix) all succeeded end to end: tests → pipeline → commit → Pages deploy. Run 20260908T114721Z is live with cross-check `ok` (99 checked / 92 verified / 7 mismatch, kick-off times matched). One intermediate run (34221979671) failed only at the commit step because a manual push landed first — the previous Pages build stayed live, as designed.
- First unattended GitHub cron run: 2026-09-09 06:00 UTC. Acceptance = a new `run_id` in `latest.json` and a green run in the Actions tab.
