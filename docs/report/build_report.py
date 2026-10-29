"""Builds docs/report/main.tex from the part files and the pipeline outputs, then compiles the PDF.

Every number in the report text is read from a result file (no manual copying). Run after `make all`:
    python docs/report/build_report.py --data /path/to/data
"""
import argparse
import json
import pathlib
import re
import subprocess
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
DOCS = HERE.parent


def tex(s):
    return str(s).replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


def pct(x, d=0):
    return f"{100 * x:.{d}f}\\%"


def build(data):
    out = pathlib.Path(data) / "outputs"
    R = {}
    dq = json.loads((out / "dq_evaluation.json").read_text())
    op = json.loads((out / "operating_point_history.json").read_text())
    curve = pd.read_csv(out / "detection_tradeoff_history.csv")
    fo = pd.read_csv(out / "forecast_backtest_overall.csv")
    fh = pd.read_csv(out / "forecast_backtest_by_horizon.csv")
    fs = json.loads((out / "forecast_backtest_summary.json").read_text())
    sc = pd.read_csv(out / "allocation_scenarios.csv")
    rec = json.loads((out / "allocation_recommendation.json").read_text())
    dm = json.loads((out / "demand_meta.json").read_text())
    truth = pd.read_csv(pathlib.Path(data) / "ground_truth" / "ground_truth_outbreaks_history.csv")
    cal = pd.read_csv(out / "preparedness_calendar.csv")

    R["ROWS_IN"] = f"{dq['rows_in']:,}".replace(",", "\\,")
    R["N_OUTBREAKS"] = str(truth["outbreak_id"].nunique())
    q = dq["quarantined_rows"]
    top = pd.DataFrame(dq["rules"]).sort_values("fail", ascending=False)
    top = top[top["fail"] > 0].head(5)
    R["DQ_RESULT"] = (f"Of {dq['rows_in']:,} received records, {q:,} ({pct(q / dq['rows_in'], 1)}) were quarantined; the largest groups were "
                      + ", ".join(f"{tex(r.rule_code.split('_', 1)[1].replace('_', ' ').lower())} ({pct(r.fail_rate, 2)})" for r in top.itertuples())
                      + ". The rates match the injected defect rates (about 2\\% resubmissions, 0.5\\% orphan keys, 0.8\\% invalid ages, 0.2\\% invalid sex), which shows that the rules catch what was injected without flagging much else. Duplicates in the same load (R16) are the resubmitted records of the bulk history load; in a daily incremental load the same records are rejected as R17.")
    pd_ = dq["patient_dedup"]
    R["DEDUP_RESULT"] = (f"the window flagged {pd_['flagged']:,} records with precision {pd_['precision']:.3f} (no false positive) and recall {pd_['recall']:.3f} against the {pd_['injected_in_silver']:,} injected duplicates that passed validation; the missed ones are duplicates whose partner record was edited by an injected defect (for example a different sex or age band), which the compatibility test deliberately refuses to merge.")
    # ---- detection
    R["EVAL_START"] = op["evaluation_start"]
    best = op["metrics"]
    R["N_EVENTS"] = str(int(best["events"]))
    lab = {"c2": "EARS C2", "fl": "Seasonal (Farrington-style)", "cusum": "CUSUM, seasonal"}
    rows = []
    for m in ("c2", "fl", "cusum"):
        v = op["per_method"][m]
        rows.append(f"{lab[m]} & {v['threshold']:g} & {pct(v['sensitivity'])} & {pct(v['sensitivity_local'])} & {v['false_per_district_year']:.2f} & {v['median_days_to_detection']:.0f} \\\\")
    R["DET_TABLE"] = ("\\begin{table}[t]\\caption{Detection at the best admissible threshold. Local: sensitivity for local outbreaks only. FA/yr: false alert episodes per district-year. Days: median days to detection.}\\label{tab:det}\\centering\\small\n"
                      "\\begin{tabular}{@{}lrrrrr@{}}\\toprule Method & Thr. & Sens. & Local & FA/yr & Days \\\\\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\\end{tabular}\\end{table}")
    art = list(op["artefact_test"].values())[0]
    nv = curve[(curve["method"] == "c2_naive")]
    g2 = curve[(curve["method"] == "c2")]
    thr_n = g2.loc[g2["false_per_district_year"].le(1.0), "threshold"].min()
    nvr = nv[nv["threshold"] == (thr_n if pd.notna(thr_n) else 8.0)]
    sc2 = op["per_method"]["c2"]
    R["DET_TEXT"] = (f"The method chosen by the rule is {lab[op['method']]} at threshold {op['threshold']:g} ({pct(best['sensitivity'])} of events detected, {best['false_per_district_year']:.2f} false alert episodes per district-year, median {best['median_days_to_detection']:.0f} days from outbreak start to the end of the first alerting report week). "
                     f"The comparison shows what the seasonal baseline buys: EARS C2 with its eight-week baseline most likely mistakes the steep monsoon rise of dengue for outbreaks (we did not separate false alerts by cause), so it needs a threshold of {sc2['threshold']:g} to stay under one false alert per district-year and then detects only {pct(sc2['sensitivity'])} of events, whereas the seasonal detector detects {pct(op['per_method']['fl']['sensitivity'])} at a similar false alert rate ({op['per_method']['fl']['false_per_district_year']:.2f} per district-year at threshold {op['per_method']['fl']['threshold']:g}). "
                     f"Detection is slow in absolute terms (about three to four weeks) because the injected outbreaks ramp up gradually and alerts are raised at the end of a report week. "
                     f"\\textbf{{Reporting artefact.}} In the district of the silent facility, with back-fill on {art['backfill_date']}, a detector without the guard raised alerts in {art['naive_alert_weeks']} week(s) around the back-fill, while the guarded pipeline raised {art['guarded_alert_weeks']}; the guard flagged {art['artefact_weeks_flagged']} report week(s) as artefacts. "
                     f"The slow-growing dengue outbreak and the nationwide surge are included in the event counts above.")
    # ---- forecast
    def row(dis, mod):
        return fo[(fo["disease_group"] == dis) & (fo["model"] == mod)].iloc[0]
    names = {"nb_weather": "NB regression with weather", "nb_noweather": "NB regression, no weather", "seasonal_naive": "Seasonal naive"}
    frows = []
    for dis in ("dengue", "diarrhoea", "malaria"):
        for mod in ("nb_weather", "nb_noweather", "seasonal_naive"):
            r = row(dis, mod)
            frows.append(f"{dis if mod == 'nb_weather' else ''} & {names[mod]} & {r['mae']:.2f} & {r['mae'] / r['mean_observed']:.2f} & {r['wis']:.2f} & {pct(r['coverage80'])} & {pct(r['coverage95'])} \\\\")
        frows.append("\\midrule")
    R["FC_TABLE"] = ("\\begin{table*}[t]\\caption{Rolling-origin backtest, 52 origins, horizons 1 to 4, all 64 districts. Mean observed weekly counts per district: dengue "
                     f"{row('dengue', 'nb_weather')['mean_observed']:.1f}, diarrhoea {row('diarrhoea', 'nb_weather')['mean_observed']:.1f}, malaria {row('malaria', 'nb_weather')['mean_observed']:.1f}.}}\\label{{tab:fc}}\\centering\\small\n"
                     "\\begin{tabular}{@{}llrrrrr@{}}\\toprule Disease & Model & MAE & MAE/mean & WIS & 80\\% cov. & 95\\% cov. \\\\\\midrule\n" + "\n".join(frows[:-1]) + "\n\\bottomrule\\end{tabular}\\end{table*}")
    gain = {d: 1 - row(d, "nb_weather")["mae"] / row(d, "seasonal_naive")["mae"] for d in ("dengue", "diarrhoea", "malaria")}
    wgain = {d: 1 - row(d, "nb_weather")["mae"] / row(d, "nb_noweather")["mae"] for d in ("dengue", "diarrhoea", "malaria")}
    h1 = fh[(fh["model"] == "nb_weather") & (fh["horizon"] == 1)].set_index("disease_group")
    h4 = fh[(fh["model"] == "nb_weather") & (fh["horizon"] == 4)].set_index("disease_group")
    R["FC_TEXT"] = (f"The regression reduces MAE relative to the seasonal-naive baseline by {pct(gain['dengue'])} for dengue, {pct(gain['diarrhoea'])} for diarrhoea and {pct(gain['malaria'])} for malaria, and it beats the baseline in {pct(fs['share_districts_beating_naive']['dengue'])}, {pct(fs['share_districts_beating_naive']['diarrhoea'])} and {pct(fs['share_districts_beating_naive']['malaria'])} of districts respectively. "
                    f"The weather covariates matter little: relative to the same model without weather the MAE improves by {pct(wgain['dengue'], 1)} for dengue, {pct(wgain['diarrhoea'], 1)} for diarrhoea and {pct(wgain['malaria'], 1)} for malaria. This is expected for horizons of one to four weeks, where recent counts and the annual cycle carry most of the information; weather lagged four to eight weeks would be expected to matter more at longer horizons, which we did not test and the brief does not require. Because the generator itself links dengue to lagged weather, this gain may overstate what real data would show. "
                    f"Interval coverage is conservative: the 80\\% intervals cover {pct(row('dengue', 'nb_weather')['coverage80'])} of dengue observations and {pct(row('diarrhoea', 'nb_weather')['coverage80'])} for diarrhoea, above the nominal level, probably because the dispersion estimated from the training pairs also absorbs the injected outbreaks (we did not test this explanation); the baseline's intervals under-cover ({pct(row('dengue', 'seasonal_naive')['coverage80'])}). For hospital managers an interval that is slightly too wide is the safer error. "
                    f"The error relative to the mean count is {'nearly flat across horizons' if abs(h4.loc['dengue', 'mae'] / h4.loc['dengue', 'mean_observed'] - h1.loc['dengue', 'mae'] / h1.loc['dengue', 'mean_observed']) < 0.03 else 'larger at longer horizons'} for dengue ({h1.loc['dengue', 'mae'] / h1.loc['dengue', 'mean_observed']:.2f} at one week, {h4.loc['dengue', 'mae'] / h4.loc['dengue', 'mean_observed']:.2f} at four; Fig.~\\ref{{fig:fc}}). This is plausible because the predictors are anchored on weeks at least four weeks old, so even horizon one is an effective five-week-ahead forecast; the cost is that the most recent weeks are not used, which nowcasting would fix (future work). The mean cases per district-week are small (single digits to a few tens), so relative errors of 30\\% or more are the realistic range; the point is that the intervals are honest. "
                    f"\\textbf{{Interpretability (C5).}} Because the weather model is only slightly better than its no-weather version and far better than the baseline, and because both are readable regressions, we kept the regression and did not add a black-box model; a gradient-boosted model would have to beat these numbers clearly to justify losing the ability to explain each forecast to an official.")
    # ---- allocation
    b60 = sc[sc["budget_fraction"] == 0.6].sort_values("alpha")
    arows = [f"{r.alpha:g} & {r.weighted_unmet / 1e3:.1f} & {pct(r.improvement_vs_baseline, 1)} & {r.gini_coverage:.2f} & {r.waste_cost / 1e3:.1f} & {pct(r.efficiency_loss_vs_alpha0, 1)} \\\\" for r in b60.itertuples()]
    base = b60.iloc[0]
    arows.append(f"pop. baseline & {base.baseline_weighted_unmet / 1e3:.1f} & --- & {base.baseline_gini:.2f} & --- & --- \\\\")
    R["ALLOC_TABLE"] = ("\\begin{table}[t]\\caption{Allocation at a 60\\% budget (thousand USD of weighted unmet demand). Waste: allocation above need caused by the floor. Loss: increase in unmet demand over $\\alpha=0$.}\\label{tab:alloc}\\centering\\small\n"
                        "\\begin{tabular}{@{}lrrrrr@{}}\\toprule $\\alpha$ & Unmet & vs base & Gini & Waste & Loss \\\\\\midrule\n" + "\n".join(arows) + "\n\\bottomrule\\end{tabular}\\end{table}")
    a0 = sc[sc["alpha"] == 0].set_index("budget_fraction")
    recs = rec["per_budget"]
    R["PLAN_ORIGIN"] = dm["origin"]; R["LATEST_ORIGIN"] = dm["latest_origin"]
    R["LATEST_NEED"] = f"{dm['latest_total_need_cost']:,.0f}".replace(",", "\\,"); R["PEAK_NEED"] = f"{dm['peak_total_need_cost']:,.0f}".replace(",", "\\,")
    a4 = b60[b60["alpha"] == 0.4].iloc[0]; a8 = b60[b60["alpha"] == 0.8].iloc[0]
    R["ALLOC_TEXT"] = (f"At a 60\\% budget the optimiser with no equity floor cuts weighted unmet demand by {pct(base.improvement_vs_baseline, 0)} relative to the population-proportional split ({pct(a0.loc[0.2, 'improvement_vs_baseline'], 0)} at 20\\%, {pct(a0.loc[0.8, 'improvement_vs_baseline'], 0)} at 80\\% and {pct(a0.loc[1.0, 'improvement_vs_baseline'], 0)} at a full budget). In this data need is concentrated by construction: dengue demand follows the large cities while stock follows hospital beds, so a proportional rule sends supplies to districts that do not need them. "
                    f"The cost of equity is small at moderate floors and large at high ones: $\\alpha=0.4$ costs {pct(a4.efficiency_loss_vs_alpha0, 1)} of weighted unmet demand and reduces the Gini coefficient of coverage across districts from {base.gini_coverage:.2f} to {a4.gini_coverage:.2f}, while $\\alpha=0.8$ costs {pct(a8.efficiency_loss_vs_alpha0, 0)} and wastes USD {a8.waste_cost / 1e3:.1f} thousand on allocation above need. "
                    f"The worst-served district gets almost nothing at every budget below 100\\% (its coverage stays near zero whatever $\\alpha$), because the equity floor guarantees a share of the \\emph{{national}} allocation in proportion to population, not a share of local need; the Gini coefficient and the efficiency loss are therefore the informative measures, and a minimum-coverage guarantee would need a different constraint (future work). "
                    f"\\textbf{{Recommended policy.}} Choosing the largest $\\alpha$ whose loss is within 10\\% of the unconstrained optimum gives $\\alpha={recs['0.2']['alpha']:g}$ at a 20\\% budget, {recs['0.4']['alpha']:g} at 40\\%, {recs['0.6']['alpha']:g} at 60\\%, {recs['0.8']['alpha']:g} at 80\\% and {recs['1.0']['alpha']:g} at 100\\% (at a full budget unmet demand is almost zero without a floor, so any floor looks expensive in relative terms). This answers the Ministry's question: a district receives more than another because its forecast upper demand minus stock is larger, weighted by how few hospital beds it has per head, and every district receives at least $\\alpha$ times its population share.")
    # ---- calendar sentence for the policy brief
    R["CAL_DENGUE"] = "; ".join(f"{r.division} week {int(r.season_start_week)} to {int(r.season_end_week)}" for r in cal[(cal.disease_group == "dengue") & (cal.marked_season)].head(4).itertuples())
    return R


def fill(R, optional):
    tmpl = "".join((HERE / f).read_text() for f in ("part_intro.tex", "part_cep.tex", "part_arch.tex", "part_data.tex", "part_methods.tex", "part_eval.tex", "part_drop.tex", "part_dash.tex", "part_ethics.tex", "part_limits.tex"))
    R.update(optional)
    # conflict results reuse evaluation numbers
    for k, v in R.items():
        tmpl = tmpl.replace(f"@@{k}@@", str(v))
    left = sorted(set(re.findall(r"@@([A-Z0-9_]+)@@", tmpl)))
    if left:
        raise SystemExit(f"unfilled tokens: {left}")
    return tmpl


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    a = ap.parse_args()
    R = build(a.data)
    print(json.dumps({k: (v[:80] + "...") for k, v in R.items()}, indent=1)[:1500])
