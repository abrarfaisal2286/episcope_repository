#!/bin/sh
# Rehearse the hidden 14-day drop with our own generator, then prove idempotency.
# Usage: sh scripts/rehearse_drop.sh      (uses EPISCOPE_DATA and the history already built by `make all`)
set -e
python -m generator.generate --drop --seed 99 --start 2026-01-01 --days 14
python -m orchestration.run_pipeline --incremental --truth-tag drop
python scripts/count_state.py > data/outputs/state_after_drop.json
python -m orchestration.run_pipeline --incremental --truth-tag drop     # second run: must change nothing
python scripts/count_state.py > data/outputs/state_after_rerun.json
diff data/outputs/state_after_drop.json data/outputs/state_after_rerun.json && echo "IDEMPOTENT: no change after re-run"
