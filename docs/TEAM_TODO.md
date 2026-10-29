# Items only the team can complete

These parts of the handover cannot be produced by code or by an assistant and must be done by the three members.

1. **Git history and tags.** Commit regularly from three different accounts, cite requirement IDs in messages
   (for example `AN-5: rolling-origin backtest`), tag `checkpoint` on Day 7 and `v1.0` before the deadline.
   Keep the repository private and share it with the instructor. Never commit `.env`, salts or API keys.
2. **Scalability run on your own hardware (ENG-2).** (Scale factors 0.01, 0.025 and 0.05 were already measured in the build environment, see `docs/scalability_results.md`; SF 0.25, 0.5 and 1 were not.) `make scaling` runs SF 0.25, 0.5 and 1.0 and writes
   `docs/scalability_results.md`. Paste the table and your machine specification into report section 6.
   At SF=1 the pipeline must finish within 45 minutes on a 16 GB laptop. If it does not, apply the optimisations in
   `scripts/spark_optimisations.py` and report before and after.
3. **Spark UI screenshots (ENG-3).** Run `python scripts/spark_optimisations.py` and keep the Spark UI open
   (http://localhost:4040) while each experiment runs. Take screenshots of the SQL and Stages tabs (before and after) and
   save them in `docs/figures/`. The numbers in `docs/spark_optimisations.json` come from the REST API of the same run.
4. **Usability test (VIS requirement, report section 7).** Ask at least two non-CS users to do the five tasks below on
   the dashboard without help. Record success, time, and comments; fix what confused them and note the change.
   Tasks: (a) find the district with the highest dengue alert this week; (b) say whether the forecast for your home
   district is going up or down and how sure the dashboard is; (c) find how many IV saline bags the model recommends for a
   district at a 60% budget; (d) explain what a hatched cell means; (e) switch to the district-officer view and say what you
   can no longer see.
5. **Real data swaps.** Replace the approximate district populations with the BBS 2022 table
   (`data/reference/districts.csv`, column `population`). If your machine can reach Open-Meteo, run
   `python -c "from generator.weather import fetch_open_meteo; import pandas as pd; fetch_open_meteo(pd.read_csv('data/reference/districts.csv'), '2020-10-01', '2025-12-31')"`
   before `make all`; the generator then uses real weather and labels it `open_meteo`. This code could not be executed in the
   environment where the repository was prepared (no access to the API), so test it and report any fix.
6. **Video (15 to 20 minutes, 1080p).** Follow the chapter list in the assignment. Show a clean run, one incremental day,
   an idempotent re-run (unchanged row counts) and a quarantine example (`data/lake/silver/quarantine`).
   Put timestamps in the README. Every member speaks at least four minutes of their own lead area, name on screen.
7. **Report personalisation.** Fill in group number, member names and IDs, repository URL, video link; sign the
   contribution matrix; write the individual reflections (Appendix C) in your own words.
