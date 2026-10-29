# EpiScope: big data surveillance, forecasting and response planning

**Video presentation: PASTE THE SHAREABLE LINK HERE (YouTube unlisted or Drive "anyone with the link")**
Chapter timestamps: PASTE HERE.

CSE 4345 Big Data Analytics, Complex Engineering Problem, Premier University Chittagong. Group number, members and IDs:
see the report cover page (`docs/EpiScope_Report.pdf`).

EpiScope ingests heterogeneous health, climate and stock data into a Spark medallion lakehouse, detects outbreaks,
forecasts cases and hospital demand, allocates scarce supplies with a linear programme, and serves a D3.js dashboard that
never exposes line-level data. All data are synthetic or public (see "Data").

## Prerequisites
* Python 3.10 to 3.12 and **Java 11 or 17** (`java -version`). Java 21 breaks Arrow-based pandas UDFs in Spark 3.5.
* 16 GB RAM laptop (tested down to 4 GB at small scale factors), 10 GB free disk for SF=1.
* `pip install -r requirements.txt` (all versions pinned) or `make setup`. Docker alternative: `docker compose up`.

## One command (ENG-1)
```
make all            # SF=0.05 by default: generate -> bronze -> silver -> gold -> analytics -> dashboard data
make all SF=1       # full scale (25 million encounters)
make serve          # dashboard at http://localhost:8000
```
`make all` creates `.env` with a random salt on first run (the salt is never committed). The same sequence without
the Makefile: `python -m orchestration.run_pipeline --sf 0.05 --seed 42` then `cd dashboard && python -m http.server`.

## Hidden 14-day test drop
1. Copy the drop files into `data/raw/encounters/` (CSV, same schema), `data/raw/stock/` (JSON lines) and, if supplied,
   `data/raw/weather/` (file names are free; nothing is hard-coded; a date in the file name is used only for partitioning).
2. Run `make incremental`. It ingests only new files (checksum ledger), rebuilds only the affected epi-weeks (late records
   update older weeks), re-scores detection, re-forecasts, re-solves the allocation scenarios and rewrites `dashboard/data/`.
3. Run `make serve`. Re-running step 2 changes nothing (idempotent).
To rehearse with our own drop: `make drop`.

## Layout and architecture
```
generator/      seeded synthetic S4-S6, weather fetch (Open-Meteo) with synthetic fallback, reference builders
pipeline/       bronze (ingest), silver (rules, de-identification, de-duplication), gold (marts), quality, streaming
analytics/      detection (EARS C2, CUSUM), forecast (NB GLM), optimise (demand, LP), calendar (STL)
serving/        gold -> aggregated, small-cell-suppressed JSON
dashboard/      D3.js v7 dashboard (index.html, app.js, data/)
orchestration/  DAG runner with retries and timing log
tests/  scripts/  docs/
```
Bronze keeps raw strings plus `_batch_id`, `_file_checksum`, `_load_ts`, `_source_file`. Silver is partitioned by
`epi_year/epi_week` of onset (late data touch only recent partitions; division is not a partition key because eight divisions
by 260 weeks would create tiny files). Gold marts are partitioned by epi-week. Tables are registered in the Spark SQL (Hive
metastore, Derby) catalog under database `episcope`. Epi-week definition: Sunday start, year of the Wednesday.

## Data
S1 boundaries: OCHA COD-AB Bangladesh ADM2 (redistributed on GitHub; simplified to 120 KB). S2 population: approximate rounded
district totals entered for teaching (replace `generator/reference.py::POP_K` or `data/reference/districts.csv` with the BBS 2022
table). S3 weather: `generator/weather.py` fetches Open-Meteo archive data per district centroid and caches it; when the API is
unreachable it falls back to a seeded synthetic monsoon model and labels rows `synthetic_fallback`. S4-S6 are synthetic.

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
| VIS-1..4 | dashboard/ | 7 |
| VIS-5 | dashboard what-if panel (precomputed scenario grid) | 7 |
| VIS-6 | dashboard (CB-safe palettes, bands, hatching, freshness stamps) | 7 |
| VIS-7 | not implemented | 9 |
| ENG-1 | Makefile, orchestration/run_pipeline.py, docker-compose.yml | 3 |
| ENG-2 | scripts/scaling_experiment.py, docs/scalability_results.md | 6 |
| ENG-3 | scripts/spark_optimisations.py, docs/spark_optimisations.json | 6 |
| ENG-4 | tests/ (unit, optimiser constraints, de-identification, incremental), retrying DAG | 3 |

## Results summary (SF=0.05, one CPU, 4 GB; see report section VI and `docs/results/`)
Detection (seasonal baseline) 89% of planted outbreaks at 0.98 false alert episodes per district-year; dengue forecast error 36% below
the seasonal-naive baseline; forecast-based allocation leaves 24% less weighted need unmet than a population split at a 60% budget;
quality rules quarantine 4.1% of records; 14 tests pass; streaming alerts median 22 s; a 14-day drop is ingested incrementally and a
re-run changes nothing (`docs/drop_rehearsal_state*.json`). Scaling was measured only at SF 0.01, 0.025 and 0.05 (about 11 to 13 minutes
for the data stages, dominated by fixed overheads); SF 0.25 to 1 must be measured by the team.

## Rebuilding the report
`pip install -r requirements-docs.txt`, then `PYTHONPATH=. python docs/make_figures.py` and
`cd docs/report && PYTHONPATH=../.. python finalize_report.py --data <data dir>` (needs pdflatex with IEEEtran). All numbers are read from
result files. Fill the placeholders (group, members, links) in `finalize_report.py` and Appendices B and C.

## Tests
`make test-fast` (unit tests, about 2 minutes) and `make test` (adds the Spark integration test of idempotent and
late-record loads, about 6 minutes).

## Known limitations
See report section 9. Populations are approximate; the usability test and the SF=1 scaling run must be executed by the team
on its own hardware (instructions in `docs/TEAM_TODO.md`).
