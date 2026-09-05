"""Central configuration for DailyPicks.

Everything that changes selection behaviour is versioned here. Selection rules
are frozen (POLICY_VERSION) before any day's outcomes are inspected; changing a
rule requires a new version string.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("DAILYPICKS_ROOT", Path(__file__).resolve().parent.parent))
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
PUBLISHED_DIR = DATA_DIR / "published"
DB_PATH = DATA_DIR / "dailypicks.sqlite"
SITE_DIR = ROOT / "site"

MODEL_VERSION = "dc-1.0.0"
POLICY_VERSION = "select-1.0.0"
SCHEMA_VERSION = "1"

# Display timezone for the website (user's choice); storage is always UTC.
DISPLAY_TZ = "America/Chicago"

# Competitions: openfootball code -> metadata. tz is the league's local kickoff timezone.
COMPETITIONS = {
    "en.1": {"name": "Premier League", "country": "England", "tz": "Europe/London", "tier": 1, "teams": 20},
    "de.1": {"name": "Bundesliga", "country": "Germany", "tz": "Europe/Berlin", "tier": 1, "teams": 18},
    "es.1": {"name": "La Liga", "country": "Spain", "tz": "Europe/Madrid", "tier": 1, "teams": 20},
    "it.1": {"name": "Serie A", "country": "Italy", "tz": "Europe/Rome", "tier": 1, "teams": 20},
    "fr.1": {"name": "Ligue 1", "country": "France", "tz": "Europe/Paris", "tier": 1, "teams": 18},
    "nl.1": {"name": "Eredivisie", "country": "Netherlands", "tz": "Europe/Amsterdam", "tier": 1, "teams": 18},
    "pt.1": {"name": "Primeira Liga", "country": "Portugal", "tz": "Europe/Lisbon", "tier": 1, "teams": 18},
    "en.2": {"name": "Championship", "country": "England", "tz": "Europe/London", "tier": 2, "teams": 24},
}
CORE_COMPETITIONS = ["en.1", "de.1", "es.1", "it.1", "fr.1"]
EXTRA_COMPETITIONS = ["nl.1", "pt.1", "en.2"]
ACTIVE_COMPETITIONS = CORE_COMPETITIONS + EXTRA_COMPETITIONS

# Seasons to ingest (openfootball folder names).
SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2012, 2027)]
CURRENT_SEASON = "2026-27"

# Chronological evaluation split (defined before any tuning).
EVAL_SPLIT = {
    "train": [f"{y}-{str(y + 1)[2:]}" for y in range(2012, 2022)],   # warm-up + fit history
    "validation": ["2022-23", "2023-24"],                            # tuning only
    "test": ["2024-25", "2025-26"],                                  # untouched until frozen
}

# ---- Model hyper-parameters (frozen after validation; see docs/EVALUATION.md) ----
MODEL = {
    "xi": 0.002,              # exponential time decay per day; FROZEN from validation grid (see docs/EVALUATION.md)
    "l2": 4.0,                # L2 penalty = 1/(2*prior_sd^2); 4.0 <=> prior sd 0.35; FROZEN from validation grid
    "l2_promoted_scale": 3.0, # promoted teams: penalty x3 (prior sd ~0.29) toward the promoted prior
    "max_goals": 20,          # score grid 0..20 per team
    "tail_tolerance": 1e-4,   # allowed mass outside grid; else fixture is flagged/PASS
    "min_team_matches": 3,    # minimum matches (this competition, history window) for a team to be rated
    "history_days": 3 * 365,  # window of history used for each fit
    "rho_bounds": (-0.5, 0.5),
}

# ---- Simulation ----
SIM = {
    "n": 10_000,              # required valid posterior-predictive score pairs per fixture
    "oversample": 1.10,       # draw extra parameter samples so that invalid draws can be discarded
    "max_rounds": 5,
}

# ---- Selection policy (POLICY_VERSION) ----
POLICY = {
    "survival_threshold": 0.80,          # applied to unrounded survival
    "integer_min_win": 0.60,             # integer lines must also have >= 60% outright win
    "max_picks": 20,
    "target_picks": 14,                  # target, not a quota
    # Market candidate order for choosing the primary line within a fixture:
    # highest qualifying match-total line first, then team lines.
    "primary_order": [
        ("match_over", 2.5), ("match_over", 2.0), ("match_over", 1.5),
        ("match_over", 1.0), ("match_over", 0.5),
        ("home_over", 1.5), ("away_over", 1.5), ("home_over", 0.5), ("away_over", 0.5),
    ],
    # Ranking across fixtures: watchlist first, then line level desc, then survival desc.
    "rank_keys": ["watchlist", "line_level", "survival"],
    "kickoff_buffer_minutes": 15,        # do not recommend fixtures kicking off within this buffer
}

# Refresh schedule
REFRESH_LOCAL_TIME = "05:30"
REFRESH_TZ = "America/Chicago"
STALE_AFTER_HOURS = 30
