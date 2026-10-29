"""Assemble main.tex (preamble, parts, appendices, bibliography), fill tokens, compile with pdflatex."""
import argparse
import json
import pathlib
import subprocess
import pandas as pd
import build_report as B

HERE = pathlib.Path(__file__).resolve().parent
DOCS = HERE.parent
ROOT = DOCS.parent

PREAMBLE = r"""\documentclass[conference]{IEEEtran}
\usepackage{graphicx,booktabs,tabularx,amsmath,amssymb,url,textcomp}
\usepackage[T1]{fontenc}
\graphicspath{{../}}
\hyphenation{epi-demio-logical}
\begin{document}
\title{EpiScope: A Big Data Surveillance, Forecasting and Response-Planning Platform for Seasonal, Tropical and Epidemic Diseases}
\author{\IEEEauthorblockN{Group @@GROUP@@}
\IEEEauthorblockA{CSE 4345 Big Data Analytics, Department of Computer Science \& Engineering, Premier University, Chittagong\\
Members: @@MEMBERS@@\\
Repository: @@REPO@@\ \ Video: @@VIDEO@@}}
\maketitle
\begin{abstract}
EpiScope is a prototype national platform that turns routine health, climate and stock data into early warnings, four-week forecasts, a supply allocation plan and a preparedness calendar, behind a privacy-preserving D3.js dashboard. It is built as an Apache Spark lakehouse with idempotent incremental loads, seventeen data-quality rules, salted pseudonymisation and complementary small-cell suppression. On a synthetic national dataset with planted outbreaks (1.3 million records at scale factor 0.05), a seasonal detector finds @@ABS_SENS@@ of injected outbreaks at about one false alert per district-year; negative-binomial forecasts reduce dengue error by @@ABS_FC@@ compared with a seasonal-naive baseline, and a linear programme with an equity floor leaves @@ABS_ALLOC@@ less weighted need unmet than a population-proportional split at a 60\% budget. The report states clearly which results were measured, on what hardware, and what remains for the team to verify at full scale.
\end{abstract}
\begin{IEEEkeywords}
big data, Apache Spark, disease surveillance, outbreak detection, forecasting, resource allocation, privacy, D3.js
\end{IEEEkeywords}
"""


def optional(data, R):
    data = pathlib.Path(data)
    out = data / "outputs"
    op = json.loads((out / "operating_point_history.json").read_text())
    sc = pd.read_csv(out / "allocation_scenarios.csv")
    fo = pd.read_csv(out / "forecast_backtest_overall.csv")
    m = op["metrics"]
    o = {}
    o["C1_RESULT"] = (f"The result: {B.tex(op['method'])} at threshold {op['threshold']:g} detects {B.pct(m['sensitivity'])} of the injected events with {m['false_per_district_year']:.2f} false alert episodes per district-year (Section~\\ref{{sec:eval}}).")
    b60 = sc[sc.budget_fraction == 0.6].set_index("alpha")
    o["C3_RESULT"] = (f"At a 60\\% budget, $\\alpha=0.4$ lowers the inequality of coverage (Gini) from {b60.loc[0.0, 'gini_coverage']:.2f} to {b60.loc[0.4, 'gini_coverage']:.2f} for a loss of {B.pct(b60.loc[0.4, 'efficiency_loss_vs_alpha0'], 1)} in weighted unmet demand, while $\\alpha=0.8$ loses {B.pct(b60.loc[0.8, 'efficiency_loss_vs_alpha0'], 0)}; we therefore recommend the largest $\\alpha$ within a 10\\% loss and show the whole curve to the Ministry.")
    d = fo[fo.disease_group == "dengue"].set_index("model")
    o["C5_RESULT"] = (f"For dengue the regression with weather has an MAE of {d.loc['nb_weather', 'mae']:.2f} against {d.loc['seasonal_naive', 'mae']:.2f} for the baseline and {d.loc['nb_noweather', 'mae']:.2f} without weather, so the readable model captures nearly all the achievable gain.")
    # streaming
    sp = DOCS / "streaming_result.json"
    if sp.exists():
        s = json.loads(sp.read_text())
        o["C4_RESULT"] = (f"In a test, {s['files']} newly landed files ({s['records']:,} records) were processed by the streaming job in a single trigger, and the median delay between file arrival and the alert table being written was {s['median_latency_s']:.0f} seconds (maximum {s['max_latency_s']:.0f} s), well inside the five-minute target.")
        o["STREAM_TEXT"] = (f"\\textbf{{Streaming.}} The Structured Streaming job reads new encounter files from a landing directory, validates and pseudonymises them with the same rules as the batch path, and maintains live counts per district, disease and report week. In a test with {s['files']} freshly copied files ({s['records']:,} records) it produced {s['alert_rows']} live count rows, of which {s['provisional_alerts']} were provisional alerts, with a median latency from file arrival to alert table of {s['median_latency_s']:.0f} s and a maximum of {s['max_latency_s']:.0f} s on one virtual CPU, within the five-minute requirement. The latency includes Spark job start-up because the test used a run-once trigger; a continuously running job would be faster.")
    else:
        o["C4_RESULT"] = "The streaming latency was not measured in the preparation environment."
        o["STREAM_TEXT"] = "\\textbf{Streaming.} The streaming job is implemented (\\texttt{pipeline/streaming/stream\\_alerts.py}) but its latency was not measured in the preparation environment."
    # tests
    tp = DOCS / "test_results.txt"
    tline = tp.read_text().strip().splitlines()[-1] if tp.exists() else "not run"
    o["TEST_TEXT"] = (f"\\textbf{{Tests.}} The test suite (\\texttt{{make test}}) result was: \\emph{{{B.tex(tline)}}}. It covers each quality rule firing on a crafted bad row, salted hashing and the absence of direct identifiers in silver, the optimiser constraints (budget, equity floor, non-negativity, transport) and optimality against the baseline, suppression properties, epi-week arithmetic against Spark, and an integration test that loads a two-month history twice, then lands a late record file and a resubmission and asserts exact count changes in a single epi-week.")
    # scaling
    rows = []
    sd = ROOT / "data_scaling"
    parts = sorted((DOCS / "scalability_partial").glob("sf_*.json"), key=lambda p: float(p.stem[3:]))
    for p in parts:
        j = json.loads(p.read_text())
        n = 0
        enc = sd / f"sf_{j['sf']}" / "raw" / "encounters"
        if enc.exists():
            for f in enc.glob("*.csv"):
                with open(f) as fh:
                    n += sum(1 for _ in fh) - 1
        rows.append((j, n))
    t = ["\\begin{table}[t]\\caption{Runtime by stage (seconds) at three scale factors on one virtual CPU, 4~GB memory. Rows = encounter records generated.}\\label{tab:scale}\\centering\\footnotesize",
         "\\begin{tabular}{@{}rrrrrrrr@{}}\\toprule SF & Rows & Gen. & Bronze & Silver & Gold & Detect & Total \\\\\\midrule"]
    for j, n in rows:
        t.append(f"{j['sf']:g} & {n:,} & {j['t_generate']:.0f} & {j['t_bronze']:.0f} & {j['t_silver']:.0f} & {j['t_gold']:.0f} & {j['t_detect']:.0f} & {j['total_seconds']:.0f} \\\\".replace(",", "\\,"))
    t.append("\\bottomrule\\end{tabular}\\end{table}")
    st = pd.read_csv(data / "logs" / "stage_times.csv")
    fc_s = float(st[st.stage == "forecast"].seconds.iloc[-1])
    sizes = ", ".join(f"{j['sf']:g}: {j['silver_mb']:.0f}~MB silver" for j, _ in rows)
    if len(rows) >= 2:
        j0, j1 = rows[0][0], rows[-1][0]
        r0, r1 = rows[0][1], rows[-1][1]
        txt = (f"Between scale factors {j0['sf']:g} and {j1['sf']:g} the data grew {r1 / r0:.1f}-fold ({r0:,} to {r1:,} records) but the total time of the data stages stayed almost constant ({j0['total_seconds']:.0f} s to {j1['total_seconds']:.0f} s). At these sizes the cost is therefore dominated by fixed overheads, chiefly Spark start-up per stage and reading 1826 small daily encounter files and 1826 stock files (bronze takes about 220 s at every scale), not by data volume. The experiment shows that the pipeline does not slow down for 1.3 million records; it does \\emph{{not}} show how it scales to 25 million, and no extrapolation to SF=1 is made. "
               f"The forecasting stage ({fc_s:.0f} s at SF=0.05) does not depend on the number of rows: it trains models per district, disease and horizon on weekly series, so its cost is set by the number of series (about 200) and the length of history. "
               f"\\textbf{{The required runs at SF=0.25, 0.5 and 1 were not performed}}: the preparation environment could not hold that volume in reasonable time. The team must run \\texttt{{make scaling}} on a 16~GB laptop, replace this table and state the machine; the target is 45 minutes at SF=1.")
    else:
        txt = "Only one scale factor completed in the preparation environment; the team must run the full scaling experiment."
    o["SCALING_TEXT"] = "\n".join(t) + "\n" + txt
    # spark optimisations
    opt = json.loads((DOCS / "spark_optimisations.json").read_text())
    r1, r2, r3, r4 = opt
    ot = ["\\begin{table}[t]\\caption{Spark optimisations, median of three runs on 1.26 million silver records. Spark REST-API metrics are in \\texttt{docs/spark\\_optimisations.json}.}\\label{tab:opt}\\centering\\footnotesize",
          "\\begin{tabularx}{\\columnwidth}{@{}Xrrr@{}}\\toprule Optimisation & Before (s) & After (s) & Speed-up \\\\\\midrule",
          f"Broadcast join of the 650-row registry & {r1['before_s']:.2f} & {r1['after_s']:.2f} & {r1['speedup']:.1f}$\\times$ \\\\",
          f"Compaction of {r4['files_before']} files to {r4['files_after']} & {r4['before_s']:.2f} & {r4['after_s']:.2f} & {r4['speedup']:.1f}$\\times$ \\\\",
          f"Adaptive query execution on a skewed aggregation & {r3['plain_aqe_off_s']:.2f} & {r3['plain_aqe_on_s']:.2f} & {r3['plain_aqe_off_s'] / r3['plain_aqe_on_s']:.2f}$\\times$ \\\\",
          f"Key salting (8 salts) & {r3['plain_aqe_off_s']:.2f} & {r3['salted_aqe_off_s']:.2f} & {r3['plain_aqe_off_s'] / r3['salted_aqe_off_s']:.2f}$\\times$ \\\\",
          f"Partition pruning, path read & {r2['before_s']:.2f} & {r2['after_s']:.2f} & {r2['before_s'] / r2['after_s']:.2f}$\\times$ \\\\",
          f"Partition pruning, catalog table & {r2.get('catalog_unpartitioned_s', float('nan')):.2f} & {r2.get('catalog_partitioned_s', float('nan')):.2f} & {(r2.get('catalog_unpartitioned_s') or 0) / (r2.get('catalog_partitioned_s') or 1):.2f}$\\times$ \\\\",
          "\\bottomrule\\end{tabularx}\\end{table}"]
    top = r3["top_districts"][0]
    o["OPT_TEXT"] = ("\n".join(ot) + "\n" +
        f"\\textbf{{Broadcast join.}} Enriching encounters with the facility registry by a sort-merge join shuffles every encounter by facility; broadcasting the 650-row registry removes that shuffle and is {r1['speedup']:.1f} times faster. The hint changes the physical plan from a sort-merge join to a broadcast hash join (to be confirmed in the Spark UI). "
        f"\\textbf{{Compaction.}} Writing with 200 partitions produced {r4['files_before']} small files; compacting to {r4['files_after']} cuts a column scan from {r4['before_s']:.2f} to {r4['after_s']:.2f} s ({r4['speedup']:.1f} times) because task scheduling and footer reads dominate small files; the silver and gold writers therefore repartition by the partition key before writing. "
        f"\\textbf{{Skew.}} The largest district ({top[0]}) holds {top[1]:,} of {r3['rows'] // 4:,} records, about 10\\%, which is far milder than a power-law skew; adaptive query execution gave a {r3['plain_aqe_off_s'] / r3['plain_aqe_on_s']:.2f}-fold gain, whereas manual salting did not help ({r3['plain_aqe_off_s'] / r3['salted_aqe_off_s']:.2f}-fold) because the extra aggregation stage costs as much as the imbalance it removes. We report this negative result rather than claim a gain: at 10\\% skew AQE is sufficient. "
        f"\\textbf{{Partition pruning.}} Pruning reduced the input partitions of a one-week query from {r2['input_partitions_without_pruning']} to {r2['input_partitions_with_pruning']}, yet at this size it did not make the query faster: reading the path pays for listing 262 partition directories and the unpartitioned copy benefits from Parquet min/max statistics on a 108~MB table (the catalog comparison gives {r2.get('catalog_partitioned_s', float('nan')):.2f} s partitioned against {r2.get('catalog_unpartitioned_s', float('nan')):.2f} s unpartitioned). We keep the layout because it is what lets late data replace only the affected weeks, and its read benefit would appear at the 25-million-record scale, which we could not test. "
        "Spark UI screenshots must be taken by the team (instructions in \\texttt{docs/TEAM\\_TODO.md}); the stage counts, shuffle bytes and task counts from the REST API of the same runs are stored in the JSON file.")
    # dashboard size and usability
    ds = sum(f.stat().st_size for f in (ROOT / "dashboard" / "data").rglob("*") if f.is_file())
    nat = (ROOT / "dashboard" / "data" / "national.json").stat().st_size
    o["DASH_SIZE"] = f"{nat / 1e6:.1f}~MB for the national bundle ({ds / 1e6:.1f}~MB for all files including the 64 district files, against the 10~MB limit)"
    o["USABILITY"] = ("It has not been carried out yet, so this report makes no usability claim. The protocol (five tasks: find the district with the highest dengue alert, read the forecast direction and its uncertainty, find the recommended IV saline for a district at a 60\\% budget, explain a hatched cell, and describe what changes in the district-officer view) and the record sheet are in \\texttt{docs/TEAM\\_TODO.md}. The team must run it with at least two non-specialist users, record success and time per task, fix what confuses them, and replace this paragraph with the findings.")
    # abstract and brief tokens
    best = R
    o["GROUP"] = "\\textbf{[number]}"; o["MEMBERS"] = "\\textbf{[Member 1 (ID)], [Member 2 (ID)], [Member 3 (ID)]}"
    o["REPO"] = "\\textbf{[URL]}"; o["VIDEO"] = "\\textbf{[link]}"
    o["ABS_SENS"] = B.pct(op["per_method"]["fl"]["sensitivity"])
    o["ABS_FC"] = B.pct(1 - d.loc["nb_weather", "mae"] / d.loc["seasonal_naive", "mae"])
    o["ABS_ALLOC"] = B.pct(sc[(sc.budget_fraction == 0.6) & (sc.alpha == 0.0)].improvement_vs_baseline.iloc[0])
    cal = pd.read_csv(out / "preparedness_calendar.csv")
    dn = cal[(cal.disease_group == "dengue") & (cal.marked_season)]
    o["B_PLAN_ORIGIN"] = R["PLAN_ORIGIN"]; o["B_LATEST_ORIGIN"] = R["LATEST_ORIGIN"]
    o["B_NEED_PEAK"] = R["PEAK_NEED"]; o["B_NEED_LATEST"] = R["LATEST_NEED"]
    o["B_IMPR60"] = B.pct(sc[(sc.budget_fraction == 0.6) & (sc.alpha == 0.0)].improvement_vs_baseline.iloc[0])
    o["B_IMPR80"] = B.pct(sc[(sc.budget_fraction == 0.8) & (sc.alpha == 0.0)].improvement_vs_baseline.iloc[0])
    o["B_ALPHA"] = B.pct(0.4); o["B_ALPHA_LOSS"] = B.pct(b60.loc[0.4, "efficiency_loss_vs_alpha0"], 1)
    o["B_GINI0"] = f"{b60.loc[0.0, 'gini_coverage']:.2f}"; o["B_GINI4"] = f"{b60.loc[0.4, 'gini_coverage']:.2f}"
    ss = dn.season_start_week.describe()
    o["B_DENGUE_SEASON"] = (f"season starts between weeks {int(dn.season_start_week.min())} and {int(dn.season_start_week.max())} depending on the division, and ends between weeks {int(dn.season_end_week.min())} and {int(dn.season_end_week.max())}" if len(dn) else "no marked season in this trial")
    pm = op["per_method"]["fl"]
    o["B_SENS"] = B.pct(pm["sensitivity"]); o["B_FA"] = f"{pm['false_per_district_year']:.1f}"; o["B_DAYS"] = f"{pm['median_days_to_detection']:.0f}"
    o["B_FC_GAIN"] = B.pct(1 - d.loc["nb_weather", "mae"] / d.loc["seasonal_naive", "mae"])
    return o


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); a = ap.parse_args()
    R = B.build(a.data)
    R.update(optional(a.data, R))
    body = B.fill(R, {})
    app = (HERE / "part_appendix.tex").read_text()
    for k, v in R.items():
        app = app.replace(f"@@{k}@@", str(v))
    pre = PREAMBLE
    for k, v in R.items():
        pre = pre.replace(f"@@{k}@@", str(v))
    tex = pre + body + "\n" + (HERE / "bib.tex").read_text() + "\n" + app + "\n\\end{document}\n"
    import re
    left = sorted(set(re.findall(r"@@([A-Z0-9_]+)@@", tex)))
    if left:
        raise SystemExit(f"unfilled tokens: {left}")
    (HERE / "main.tex").write_text(tex)
    for _ in range(2):
        r = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"], cwd=HERE, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-3000:]); raise SystemExit("pdflatex failed")
    print("compiled", HERE / "main.pdf")


if __name__ == "__main__":
    main()
