# AI usage statement

Tools used: Claude (Anthropic) as a coding and writing assistant.

Purposes: designing the repository layout and the data contract, drafting the generator, Spark stages, analytics
modules, D3 dashboard and tests, debugging runtime errors, and drafting the report text.

Representative prompts (edit this list to match what each member actually asked):
1. "Split the EpiScope CEP assignment into three roles with tasks and talking points for the video."
2. "Build the end-to-end pipeline (generator, Spark bronze/silver/gold, detection, forecasting, optimiser, D3 dashboard)."
3. "Debug: pandas UDFs fail on Java 21 with Arrow (sun.misc.Unsafe not available)."

Rules we follow: every member has read and run the code they are responsible for, can explain it in the oral
verification, and has replaced any assumption in `analytics/optimise/assumptions.json` that they disagree with.
Third-party code: D3.js v7 (ISC licence, vendored in `dashboard/vendor/`). Boundary data: OCHA COD-AB Bangladesh ADM2
as redistributed on GitHub (mostakimjihad/bd_district_geojson); check the licence before reuse.
