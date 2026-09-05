#!/bin/bash
# Hyper-parameter grid on the VALIDATION split only. Test split is untouched here.
cd "$(dirname "$0")/.."
for xi in 0.001 0.002 0.004; do
  for l2 in 1 2 4; do
    echo "=== xi=$xi l2=$l2 $(date -u +%FT%TZ)"
    python3 -m dailypicks.evaluate validation --xi $xi --l2 $l2 --tag "xi${xi}_l2${l2}" --draws 300
  done
done
echo "=== DONE $(date -u +%FT%TZ)"
