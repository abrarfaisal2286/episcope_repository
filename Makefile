# EpiScope: one command rebuilds the lake, analytics and dashboard data.   make all
PY      ?= python
SF      ?= 0.05
SEED    ?= 42
export PYTHONPATH := $(CURDIR)

.PHONY: all setup incremental drop serve test test-fast scaling clean env

env:
	@test -f .env || ($(PY) -c "import secrets;print('EPISCOPE_SALT='+secrets.token_hex(24));print('SPARK_DRIVER_MEMORY=2g');print('SHUFFLE_PARTITIONS=8')" > .env && echo "created .env with a random salt")

setup:
	$(PY) -m pip install -r requirements.txt

all: env
	$(PY) -m orchestration.run_pipeline --sf $(SF) --seed $(SEED)

# Process whatever new files are in data/raw (for example the hidden 14-day drop). Does not regenerate data.
incremental: env
	$(PY) -m orchestration.run_pipeline --incremental

# Generate our own 14-day drop in the same schema, then ingest it incrementally.
drop: env
	$(PY) -m generator.generate --drop --seed 99 --start 2026-01-01 --days 14
	$(PY) -m orchestration.run_pipeline --incremental --truth-tag drop

serve:
	cd dashboard && $(PY) -m http.server 8000

test-fast:
	$(PY) -m pytest -q -m "not slow"

test:
	$(PY) -m pytest -q

scaling: env
	$(PY) scripts/scaling_experiment.py 0.25 0.5 1.0

clean:
	rm -rf data/raw data/lake data/outputs data/logs data/ground_truth data/generator_state.json dashboard/data/*.json dashboard/data/district
