#!/usr/bin/env bash
# Daily refresh entrypoint used by GitHub Actions (and runnable locally).
# Gate: the full job runs at 01:00 Africa/Lagos (WAT, UTC+1, no DST) = 00:00 UTC.
# The 15:00 UTC slot runs a lighter status refresh (same pipeline; cheap because
# the dataset and fits are small) so started/postponed fixtures drop off before kick-off.
set -euo pipefail
cd "$(dirname "$0")/.."
HOUR=$(TZ=Africa/Lagos date +%H)
UTC_HOUR=$(date -u +%H)
FORCE="${FORCE:-false}"
TRIGGER="${TRIGGER:-manual}"
if [[ "$FORCE" != "true" && "$TRIGGER" == "schedule" ]]; then
  if [[ "$UTC_HOUR" == "00" ]]; then
    if [[ "$HOUR" != "01" ]]; then echo "Not 01:xx Africa/Lagos (local hour $HOUR); skipping."; exit 0; fi
    TRIGGER="scheduled-0100-wat"
  else
    TRIGGER="scheduled-status-refresh"
  fi
fi
echo "Running pipeline (trigger=$TRIGGER, local WAT hour=$HOUR)"
python -m dailypicks.pipeline --trigger "$TRIGGER" | tee logs_run.json
STATUS=$(python -c "import json;print(json.load(open('logs_run.json'))['status'])")
python scripts/build_site.py
mkdir -p site/dist/pages && cp site/dist/standalone.html site/dist/pages/index.html && cp data/published/latest.json site/dist/pages/latest.json
if [[ "$STATUS" != "published" ]]; then
  echo "::error::Pipeline run ended with status $STATUS — previous board preserved"; cat logs_run.json; exit 1
fi
