# EpiScope: big data surveillance, forecasting and response planning

EpiScope ingests health, climate and stock data into a Spark lakehouse (bronze, silver, gold), detects outbreaks, forecasts cases
and hospital demand, allocates scarce supplies with a linear programme, and serves a D3.js dashboard that never exposes
line-level data. All data are synthetic or public.

## Quick start

**Prerequisites:** Python 3.10 to 3.12 and **Java 11 or 17** (`java -version`; Java 21 does not work with this Spark version),
16 GB RAM (it also runs at small scale on 4 GB), 10 GB free disk for full scale, and `make`
(on Windows use Git Bash, WSL or Docker).

```
make setup          # install pinned dependencies (pip install -r requirements.txt)
make all            # one command: generate -> bronze -> silver -> gold -> analytics -> dashboard data
make serve          # dashboard at http://localhost:8000
```

`make all` uses scale factor 0.05 (about 1.3 million records). For the full 25 million records use `make all SF=1`.
The first run creates a `.env` file with a random salt for hashing patient identifiers; it is never committed.

Tests: `make test-fast` (about 2 minutes) or `make test` (adds the slow Spark integration test, about 8 minutes).

## Running the hidden 14-day test drop

1. Copy the drop files into `data/raw/encounters/` (CSV), `data/raw/stock/` (JSON lines) and, if supplied, `data/raw/weather/`.
   File names are free and nothing is hard-coded.
2. Run `make incremental`. It ingests only new files, rebuilds only the affected epi-weeks (late records update older weeks),
   re-scores alerts, re-forecasts, re-solves the allocation scenarios and rewrites `dashboard/data/`.
3. Run `make serve`. Running step 2 again changes nothing (idempotent).

To rehearse with our own drop: `make drop`.

## Architecture

```
generator/      synthetic encounters, registry, stock; weather fetch (Open-Meteo) with synthetic fallback
pipeline/       bronze (ingest), silver (17 rules, de-identification, de-duplication), gold (marts), quality, streaming
analytics/      detection (EARS C2, seasonal, CUSUM), forecast (negative-binomial), optimise (demand, LP), calendar (STL)
serving/        gold -> aggregated, small-cell-suppressed JSON
dashboard/      D3.js v7 dashboard (index.html, app.js, data/)
orchestration/  pipeline runner with retries and timing log
tests/  scripts/  docs/
```

Bronze keeps raw data unchanged plus batch id, file checksum and load time. Silver is typed, validated, de-identified (salted hash,
age bands) and partitioned by `epi_year/epi_week`. Gold holds the epi-week marts, report completeness and the data-quality report;
tables are registered in the Spark SQL catalog under database `episcope`. Epi-weeks start on Sunday and belong to the year of their Wednesday.

## Requirement traceability
| ID | Where | Report section |
|---|---|---|
| ING-1 | generator/generate.py, pipeline/bronze/ingest.py | 4 |
| ING-2 | pipeline/*, analytics/detection, analytics/forecast (Spark) | 3 |
| ING-3 | pipeline/silver/build.py, pipeline/gold/build.py, common/spark_utils.py (catalog) | 3, 4 |
| ING-4 | pipeline/silver/build.py (ledger, partition merge), tests/test_incremental.py | 4 |
| ING-5 | not implemented (local file system; storage paths isolated in common/config.py) | 9 |
| ING-6 | pipeline/streaming/stream_alerts.py | 4, 6 |
| ING-7 | not implemented (Parquet reporting triangle gives "what did we know on date t") | 9 |
| DQ-1 | pipeline/quality/rules.py, engine.py (17 encounter rules, quarantine with reason codes) | 4 |
| DQ-2 | gold/dq_report, dashboard data-quality panel | 4, 7 |
| DQ-3 | `source_batch_ids` in gold marts; scripts/trace.py | 4 |
| DQ-4 | pipeline/silver/build.py (patient window), pipeline/quality/evaluate_dq.py | 4, 6 |
| PRV-1 | pipeline/silver/build.py (salted SHA-256, age bands), tests/test_deidentification.py | 4 |
| PRV-2 | serving/suppress.py, serving/export.py, tests/test_suppression.py | 4, 7 |
| PRV-3 | serving/export.py (division_view.json, district/*.json), dashboard role selector | 7 |
| PRV-4 | not implemented (discussion in report section 8) | 8 |
| AN-1 | analytics/detection/detect.py | 5 |
| AN-2 | analytics/detection/evaluate.py | 6 |
| AN-3 | analytics/detection/evaluate.py (C2 vs CUSUM, artefact test) | 6 |
| AN-4 | analytics/forecast/forecast.py | 5 |
| AN-5 | analytics/forecast/evaluate.py | 6 |
| AN-6 | analytics/optimise/demand.py, assumptions.json | 5 |
| AN-7 | analytics/optimise/allocate.py | 5, 6 |
| AN-8 | analytics/optimise/allocate.py (scenario grid), dashboard what-if | 6 |
| AN-9 | analytics/calendar/seasonal_calendar.py | 5 |
| AN-10 | not implemented | 9 |
| VIS-1 to VIS-4 | dashboard/ | 7 |
| VIS-5 | dashboard what-if panel (precomputed scenario grid) | 7 |
| VIS-6 | dashboard (CB-safe palettes, bands, hatching, freshness stamps) | 7 |
| VIS-7 | not implemented | 9 |
| ENG-1 | Makefile, orchestration/run_pipeline.py, docker-compose.yml | 3 |
| ENG-2 | scripts/scaling_experiment.py, docs/scalability_results.md | 6 |
| ENG-3 | scripts/spark_optimisations.py, docs/spark_optimisations.json | 6 |
| ENG-4 | tests/ (unit, optimiser constraints, de-identification, incremental), retrying DAG | 3 |

The technical report is `docs/EpiScope_Report.pdf`; measured results are in `docs/RESULTS.md`.
