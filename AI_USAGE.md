# AI usage statement

Tools used: Claude (Anthropic), as a coding and writing assistant.

Purposes: designing the repository layout and the data contract between components, drafting the generator, the Spark stages,
the analytics modules, the D3.js dashboard and the tests, debugging runtime errors, and drafting the report and video scripts.

Representative prompts:
1. "Read the assignment and divide the work among the group members, with explanations for the video presentation."
2. "Build the end-to-end pipeline: generator, Spark bronze, silver and gold layers, outbreak detection, forecasting, allocation optimiser and the D3.js dashboard."
3. "Debug: pandas UDFs fail on Java 21 with Arrow (sun.misc.Unsafe not available)."
4. "Write the technical report in the format required by the assignment, with the policy brief."

All generated code was run and tested (see `tests/`, 14 tests), and every member is responsible for being able to explain
the code in their own area.

Third-party code: D3.js v7 (ISC licence, vendored in `dashboard/vendor/`). Boundary data: OCHA COD-AB Bangladesh ADM2 as
redistributed on GitHub (mostakimjihad/bd_district_geojson).
