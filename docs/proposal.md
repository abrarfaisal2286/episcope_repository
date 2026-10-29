# EpiScope proposal (M1)

Course CSE 4345 Big Data Analytics, Complex Engineering Problem. Group of three.

## 1. Problem restatement
The Ministry of Health needs a prototype platform that turns routine facility, laboratory, climate and stock data into
(i) early warnings for dengue, acute diarrhoeal disease, influenza-like illness (ILI) and malaria per district,
(ii) one to four week forecasts of cases and hospital demand, (iii) an allocation of a fixed national stock of supplies, and
(iv) a seasonal preparedness calendar. Users are not data scientists, data arrive late and incomplete, and no identifiable
patient information may reach the dashboard. Real line-level data are unavailable, so we combine real boundaries and
weather with a seeded synthetic encounter generator that contains injected, recorded outbreaks.

## 2. Stakeholders and conflicts
| Stakeholder | Main need | Concern |
|---|---|---|
| Ministry policymakers | defensible allocation of a fixed budget | why district X received more than Y |
| Surveillance epidemiologists | transparent methods, adjustable thresholds | reporting artefacts mistaken for outbreaks |
| District health officers | early local warning and own stock | alert fatigue, late supplies |
| Hospital managers | bed and consumable forecasts | forecasts without uncertainty |
| Privacy regulator, public | minimal data, no re-identification | small counts, line-level data leaving secure zones |

C1 Sensitivity versus false alarms: we plot the trade-off for two detectors and choose the operating point that maximises
sensitivity subject to at most one false alert episode per district-year.
C2 Granularity versus privacy: district level only; counts of 1 to 4 are suppressed, with complementary suppression so
subtraction cannot recover them.
C3 Efficiency versus equity: a linear programme with an equity floor alpha; efficiency-equity curve over five budgets.
C4 Freshness versus cost: daily micro-batch for analytics (decisions are weekly); a streaming path gives provisional alerts
for the current week within minutes.
C5 Accuracy versus interpretability: a negative-binomial regression with lagged weather, compared with a seasonal-naive
baseline and a no-weather ablation; no black-box model unless it clearly wins.

## 3. Architecture and tools (justified)
Medallion lakehouse on Parquet with Apache Spark (PySpark, local mode, Spark SQL/Hive catalog): bronze (raw, immutable,
with checksum and batch id), silver (validated, de-identified, de-duplicated), gold (epi-week marts, report completeness,
weather, stock, quality report). Python modules per stage, a retrying DAG runner, SciPy HiGHS for optimisation,
statsmodels for regression, D3.js v7 for the dashboard. Everything runs on a 16 GB laptop.

## 4. Diseases and methods
Dengue (A90/A91), diarrhoeal disease (A00/A09), ILI (J10/J11), malaria (B50 to B54). Detection: EARS C2 and CUSUM on
report-week counts with an artefact guard. Forecasting: per-district negative-binomial GLM, rolling-origin backtest.
Allocation: LP with budget, equity floor and transport capacity. Calendar: STL seasonal decomposition per division.

## 5. Roles
Member 1 data engineering (generator, ingestion, quality, lineage, performance). Member 2 analytics (detection,
forecasting, optimiser, calendar, evaluation). Member 3 visualisation (serving layer, privacy suppression, D3 dashboard,
usability test). All members write the report and contribute commits outside their own role.

## 6. Risk register
| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | Pipeline too slow at SF=1 on 16 GB | medium | high | partition pruning, broadcast joins, AQE, scaling study by Day 11 |
| 2 | Hidden drop breaks the pipeline | medium | high | no hard-coded dates or file names, rehearsal on our own drop |
| 3 | Detection flags reporting artefacts | high | medium | late-share artefact guard, evaluated against injected artefact |
| 4 | Small counts leak through the dashboard | medium | high | suppression module with property tests |
| 5 | Forecasts unreliable in sparse districts | high | medium | prediction intervals, intercept-seasonal fallback, baseline comparison |
| 6 | Member unavailable or uneven commits | medium | medium | cross-role code review, commit audit with git shortlog |
| 7 | Open-Meteo API limits or outage | medium | low | cache every response in bronze, synthetic fallback labelled |
