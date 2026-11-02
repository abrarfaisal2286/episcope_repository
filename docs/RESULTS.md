# Results summary

Detection (seasonal baseline) 89% of planted outbreaks at 0.98 false alert episodes per district-year; dengue forecast error 36% below
the seasonal-naive baseline; forecast-based allocation leaves 24% less weighted need unmet than a population split at a 60% budget;
quality rules quarantine 4.1% of records; 14 tests pass; streaming alerts median 22 s; a 14-day drop is ingested incrementally and a
re-run changes nothing (`docs/drop_rehearsal_state*.json`). Scaling was measured only at SF 0.01, 0.025 and 0.05 (about 11 to 13 minutes
for the data stages, dominated by fixed overheads); SF 0.25 to 1 was not run.

Detail: `docs/results/` (CSV and JSON), `docs/scalability_results.md`, `docs/spark_optimisations.json`, and the report.
