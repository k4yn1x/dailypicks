#!/usr/bin/env bash
# Daily refresh entrypoint used by GitHub Actions (and runnable locally).
# Gate: only run the full job when local America/Chicago time is 05:xx, so the two
# UTC cron slots (10:30 / 11:30) resolve to exactly one 05:30 local run across DST.
# The 15:00 UTC slot runs a lighter status refresh (same pipeline; cheap because
# the dataset and fits are small) so started/postponed fixtures drop off before kick-off.
set -euo pipefail
cd "$(dirname "$0")/.."
HOUR=$(TZ=America/Chicago date +%H)
UTC_HOUR=$(date -u +%H)
FORCE="${FORCE:-false}"
TRIGGER="${TRIGGER:-manual}"
if [[ "$FORCE" != "true" && "$TRIGGER" == "schedule" ]]; then
  if [[ "$UTC_HOUR" == "10" || "$UTC_HOUR" == "11" ]]; then
    if [[ "$HOUR" != "05" ]]; then echo "Not 05:xx America/Chicago (local hour $HOUR); skipping this slot."; exit 0; fi
    TRIGGER="scheduled-0530-chicago"
  else
    TRIGGER="scheduled-status-refresh"
  fi
fi
echo "Running pipeline (trigger=$TRIGGER, local Chicago hour=$HOUR)"
python -m dailypicks.pipeline --trigger "$TRIGGER" | tee logs_run.json
STATUS=$(python -c "import json;print(json.load(open('logs_run.json'))['status'])")
python scripts/build_site.py
mkdir -p site/dist/pages && cp site/dist/standalone.html site/dist/pages/index.html && cp data/published/latest.json site/dist/pages/latest.json
if [[ "$STATUS" != "published" ]]; then
  echo "::error::Pipeline run ended with status $STATUS — previous board preserved"; cat logs_run.json; exit 1
fi
