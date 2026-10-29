"""Gold -> aggregated, suppressed JSON for the D3 dashboard (VIS-4, PRV-2, PRV-3).

Only aggregates leave this module: district x disease x week counts (small cells suppressed), model
outputs and allocation scenarios. No line-level or patient-level data is read here (silver is never opened).
Outputs in dashboard/data/: national.json, division_view.json, district/<id>.json, meta.json, quality.json,
calendar.json, evaluation.json, districts.geojson (built once from reference data).
"""
import json
import pathlib
import shutil
import numpy as np
import pandas as pd
from common import config
from common.io import read_table
from serving.suppress import suppress_value, suppress_group

OUT = config.SERVING
N_ALERT_WEEKS = 104
DIS = config.DISEASES


def dump(obj, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False, default=_default))


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.date().isoformat()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(type(o))


def _clean(v, nd=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return round(float(v), nd)


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    meta_p = json.loads((config.OUTPUTS / "pipeline_meta.json").read_text())
    fmeta = json.loads((config.OUTPUTS / "forecast_meta.json").read_text())
    op_path = config.OUTPUTS / "operating_point.json"
    op = json.loads(op_path.read_text()) if op_path.exists() else dict(method="c2", threshold=3.0, min_count=5, metrics={})
    dist = pd.read_csv(config.REFERENCE / "districts.csv").sort_values("district_id").reset_index(drop=True)
    ids = dist["district_id"].tolist()
    divs = sorted(dist["division"].unique())

    wc = read_table("gold", "weekly_cases")
    wc = wc[wc["disease_group"].isin(DIS)].copy()
    wc["week_start"] = pd.to_datetime(wc["week_start"])
    last_full = pd.Timestamp(fmeta["op_origin"])
    wc = wc[wc["week_start"] <= last_full]
    weeks = sorted(wc["week_start"].unique())
    widx = {w: i for i, w in enumerate(weeks)}
    W = len(weeks)

    def cube(df, col, key="week_start"):
        a = np.zeros((len(ids), len(DIS), W), dtype=int)
        di = {d: i for i, d in enumerate(ids)}; ki = {k: i for i, k in enumerate(DIS)}
        for r in df[["district_id", "disease_group", key, col]].itertuples(index=False):
            w = pd.Timestamp(r[2])
            if w in widx:
                a[di[r[0]], ki[r[1]], widx[w]] = r[3]
        return a

    cases = cube(wc, "cases")
    wr = read_table("gold", "weekly_reports")
    wr = wr[wr["disease_group"].isin(DIS)].copy()
    wr["report_week_start"] = pd.to_datetime(wr["report_week_start"])
    reported = cube(wr.rename(columns={"n_reports": "cases"}), "cases", key="report_week_start")

    div_of = dist["division"].to_numpy()
    # ---- suppression: district cells, division totals (complementary), national totals (complementary)
    shown = np.empty(cases.shape, dtype=object); shown_rep = np.empty(cases.shape, dtype=object)
    div_tot = {d: np.empty((len(DIS), W), dtype=object) for d in divs}
    nat = np.empty((len(DIS), W), dtype=object)
    for src, dst, store_totals in ((cases, shown, True), (reported, shown_rep, False)):
        for k in range(len(DIS)):
            for w in range(W):
                div_true = []
                for d in divs:
                    m = np.where(div_of == d)[0]
                    cells, tot = suppress_group([int(src[i, k, w]) for i in m])
                    for i, c in zip(m, cells):
                        dst[i, k, w] = c
                    div_true.append(int(src[m, k, w].sum()))
                    if store_totals:
                        div_tot[d][k, w] = tot
                if store_totals:
                    dcells, ntot = suppress_group(div_true)
                    # a suppressed division total (by group rule) must also be hidden at division level
                    for d, c in zip(divs, dcells):
                        if c is None:
                            div_tot[d][k, w] = None
                    nat[k, w] = ntot
    # ---- detection arrays (last N weeks)
    sc = read_table("gold", "detection_scores")
    sc["report_week_start"] = pd.to_datetime(sc["report_week_start"])
    aw = weeks[-N_ALERT_WEEKS:]
    aidx = {w: i for i, w in enumerate(aw)}
    di = {d: i for i, d in enumerate(ids)}; ki = {k: i for i, k in enumerate(DIS)}
    A = {n: np.full((len(ids), len(DIS), len(aw)), np.nan) for n in ("z", "cusum", "bm", "bsd", "zf", "bmf", "bsdf")}
    ART = np.zeros((len(ids), len(DIS), len(aw)), dtype=int)
    for r in sc[sc["report_week_start"].isin(aw)].itertuples(index=False):
        a, b, c = di[r.district_id], ki[r.disease_group], aidx[r.report_week_start]
        A["z"][a, b, c] = r.z_c2; A["cusum"][a, b, c] = r.cusum; A["zf"][a, b, c] = r.z_fl
        if r.baseline_mean_fl >= 5:
            A["bmf"][a, b, c] = r.baseline_mean_fl; A["bsdf"][a, b, c] = r.baseline_sd_fl
        if r.baseline_mean >= 5:                      # baseline means below 5 are not shown (small-cell policy)
            A["bm"][a, b, c] = r.baseline_mean; A["bsd"][a, b, c] = r.baseline_sd
        ART[a, b, c] = int(r.artefact_flag)
    # ---- forecasts (operational origin)
    fc = read_table("gold", "forecasts")
    fc = fc[(~fc["is_backtest"]) & (fc["model"] == "nb_weather")].copy()
    fc["target_week"] = pd.to_datetime(fc["target_week"])
    tw = sorted(fc["target_week"].unique())
    FC = {}
    for (d, k), g in fc.groupby(["district_id", "disease_group"]):
        g = g.set_index("target_week").reindex(tw)
        FC.setdefault(d, {})[k] = dict(
            mean=[suppress_value(None if np.isnan(v) else int(round(v))) for v in g["mean"]],
            lo=[suppress_value(None if np.isnan(v) else int(round(v))) for v in g["q10"]],
            hi=[suppress_value(None if np.isnan(v) else int(round(v))) for v in g["q90"]])
    # ---- allocation, demand, calendar, quality, evaluation
    scen = pd.read_csv(config.OUTPUTS / "allocation_scenarios.csv")
    grid = pd.read_csv(config.OUTPUTS / "allocation_grid.csv")
    rec = json.loads((config.OUTPUTS / "allocation_recommendation.json").read_text())
    dem = pd.read_csv(config.OUTPUTS / "demand.csv")
    beds = pd.read_csv(config.OUTPUTS / "bed_demand.csv")
    wts = pd.read_csv(config.OUTPUTS / "district_weights.csv")
    assume = json.loads((pathlib.Path(__file__).resolve().parents[1] / "analytics/optimise/assumptions.json").read_text())
    alloc = {}
    for sid, g in grid.groupby("scenario_id"):
        alloc[sid] = {d: {rr.resource: [int(round(x)) for x in (rr.need, rr.stock, rr.allocation, rr.baseline_allocation)]
                          for rr in gg.itertuples()} for d, gg in g.groupby("district_id")}
    # data quality
    dq = read_table("gold", "dq_report")
    dq["month"] = dq["source_date"].astype(str).str[:7]
    base = dq.drop_duplicates(["batch_id", "source"])
    qual = dict(
        monthly=[dict(source=s, month=m, rows_in=int(g["rows_in"].sum()))
                 for (s, m), g in base.groupby(["source", "month"])],
        rules=[dict(source=s, rule=r, fail=int(g["rule_fail_count"].sum()), rows=int(g["rows_in"].sum()),
                    pass_rate=round(1 - g["rule_fail_count"].sum() / max(1, g["rows_in"].sum()), 5))
               for (s, r), g in dq.groupby(["source", "rule_code"])])
    enc = dq[dq["source"] == "encounters"]
    enc_monthly = (enc.groupby(["month", "rule_code"])["rule_fail_count"].sum().reset_index()
                   .groupby("month")["rule_fail_count"].sum())
    qm = base[base["source"] == "encounters"].groupby("month")["rows_in"].sum()
    qual["encounter_monthly"] = [dict(month=m, rows_in=int(qm[m]), rule_failures=int(enc_monthly.get(m, 0))) for m in qm.index]
    ev = dict(detection=pd.read_csv(config.OUTPUTS / "detection_tradeoff_history.csv").replace({np.nan: None}).to_dict("records")
              if (config.OUTPUTS / "detection_tradeoff_history.csv").exists() else [],
              operating_point=op)
    summ_p = config.OUTPUTS / "forecast_backtest_overall.csv"
    ev["forecast"] = pd.read_csv(summ_p).replace({np.nan: None}).to_dict("records") if summ_p.exists() else []
    dmeta = json.loads((config.OUTPUTS / "demand_meta.json").read_text()) if (config.OUTPUTS / "demand_meta.json").exists() else {}
    cal = json.loads((config.OUTPUTS / "preparedness_calendar.json").read_text()) if (config.OUTPUTS / "preparedness_calendar.json").exists() else []
    meta = dict(
        built_at=meta_p["built_at"], latest_report_date=meta_p["latest_report_date"], forecast_origin=fmeta["op_origin"],
        mature_until=fmeta["mature_cutoff"], operating_point=dict(method=op["method"], threshold=op["threshold"],
                                                                 min_count=op.get("min_count", 5)),
        diseases=DIS, small_cell_rule="Counts of 1 to 4 are suppressed and shown as blank (<5).",
        data_note="Synthetic teaching data. Boundaries: OCHA COD-AB. Populations are approximate.",
        weather_source=_weather_source(), resources=assume["resources"], horizons=4,
        recommended=rec["per_budget"], recommendation_rule=rec["rule"], planning=dmeta)
    dump(meta, OUT / "meta.json")
    dump(dict(weeks=[w.date().isoformat() for w in weeks]), OUT / "weeks.json")

    districts_meta = [dict(id=r.district_id, name=r.district, division=r.division, population=int(r.population))
                      for r in dist.itertuples()]
    wtd = wts.set_index("district_id")

    def series_for(i):
        return {DIS[k]: dict(cases=[None if shown[i, k, w] is None else int(shown[i, k, w]) for w in range(W)],
                             reported=[None if shown_rep[i, k, w] is None else int(shown_rep[i, k, w]) for w in range(W)],
                             z=[_clean(x) for x in A["z"][i, k]], cusum=[_clean(x) for x in A["cusum"][i, k]], zf=[_clean(x) for x in A["zf"][i, k]],
                             bmf=[_clean(x, 1) for x in A["bmf"][i, k]], bsdf=[_clean(x, 1) for x in A["bsdf"][i, k]],
                             bm=[_clean(x, 1) for x in A["bm"][i, k]], bsd=[_clean(x, 1) for x in A["bsd"][i, k]],
                             art=ART[i, k].tolist(), fc=FC.get(ids[i], {}).get(DIS[k]))
                for k in range(len(DIS))}

    def trim(s):                                   # alert arrays only cover the last N weeks
        return s

    national = dict(districts=districts_meta, weeks=[w.date().isoformat() for w in weeks],
                    alert_weeks=[w.date().isoformat() for w in aw], forecast_weeks=[w.date().isoformat() for w in tw],
                    series={ids[i]: series_for(i) for i in range(len(ids))},
                    division_totals={d: {DIS[k]: [None if v is None else int(v) for v in div_tot[d][k]] for k in range(len(DIS))} for d in divs},
                    national_totals={DIS[k]: [None if v is None else int(v) for v in nat[k]] for k in range(len(DIS))},
                    allocation=dict(resources=list(assume["resources"]), scenarios=scen.round(4).to_dict("records"),
                                    recommended=rec["per_budget"], grid=alloc,
                                    weights={d: dict(severity=_clean(wtd.loc[d, "severity_weight"], 3), pop_share=_clean(wtd.loc[d, "population_share"], 5)) for d in ids}),
                    demand=[dict(d=r.district_id, r=r.resource, mean=int(round(r.demand_mean)), upper=int(round(r.demand_upper)),
                                 lower=int(round(r.demand_lower)), stock=int(round(r.stock))) for r in dem.itertuples()],
                    beds=[dict(d=r.district_id, upper=_clean(r.beds_upper, 1), mean=_clean(r.beds_mean, 1), free=_clean(r.free_beds, 0),
                               capacity=_clean(r.bed_capacity, 0), gap=_clean(r.bed_gap_upper, 1)) for r in beds.itertuples()])
    dump(national, OUT / "national.json")
    # ---- division view (PRV-3): aggregates only
    pop_div = dist.groupby("division")["population"].sum().to_dict()
    dv = dict(weeks=national["weeks"], divisions=[
        dict(name=d, population=int(pop_div[d]),
             totals={DIS[k]: [None if v is None else int(v) for v in div_tot[d][k]] for k in range(len(DIS))}) for d in divs],
        districts=[dict(id=x["id"], name=x["name"], division=x["division"], population=x["population"]) for x in districts_meta],
        national_totals=national["national_totals"], forecast_weeks=national["forecast_weeks"])
    dump(dv, OUT / "division_view.json")
    # ---- district officer views: own district in detail, others only in aggregate (division_view.json)
    shutil.rmtree(OUT / "district", ignore_errors=True)
    for i, d in enumerate(ids):
        own = dict(district=districts_meta[i], weeks=national["weeks"], alert_weeks=national["alert_weeks"],
                   forecast_weeks=national["forecast_weeks"], series=series_for(i),
                   demand=[x for x in national["demand"] if x["d"] == d], beds=[x for x in national["beds"] if x["d"] == d],
                   allocation={sid: g[d] for sid, g in alloc.items() if d in g})
        dump(own, OUT / "district" / f"{d}.json")
    dump(dict(scenarios=national["allocation"]["scenarios"], recommended=rec["per_budget"], resources=list(assume["resources"])), OUT / "scenarios.json")
    dump(qual, OUT / "quality.json")
    dump(cal, OUT / "calendar.json")
    dump(ev, OUT / "evaluation.json")
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    return dict(total_bytes=size, total_mb=round(size / 1e6, 2), weeks=W, districts=len(ids))


def _weather_source():
    try:
        w = read_table("silver", "weather", columns=["source"])
        return sorted(w["source"].unique().tolist())
    except Exception:
        return []


if __name__ == "__main__":
    r = build()
    print(r)
    if r["total_bytes"] > 10 * 1024 * 1024:
        raise SystemExit("dashboard data exceeds the 10 MB limit (VIS-4)")
