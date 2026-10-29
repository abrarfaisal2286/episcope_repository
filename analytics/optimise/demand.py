"""Convert case forecasts to resource demand with uncertainty (AN-6).

Demand (mean and upper) over the next 4 weeks per district and resource, using stated conversion
assumptions (analytics/optimise/assumptions.json) and district admission rates estimated from the
last 52 weeks (shrunk to the national rate). The upper demand sums the weekly 90% forecast quantiles,
a deliberately conservative bound that treats the weeks as perfectly correlated.
"""
import json
import pathlib
import numpy as np
import pandas as pd
from common import config
from common.io import read_table

ASSUME = json.loads((pathlib.Path(__file__).parent / "assumptions.json").read_text())
RESOURCES = list(ASSUME["resources"])


def admission_rates(weekly: pd.DataFrame, asof=None) -> pd.DataFrame:
    wk = pd.to_datetime(weekly["week_start"])
    last = pd.Timestamp(asof) if asof is not None else wk.max()
    w = weekly[(wk > last - pd.Timedelta(weeks=60)) & (wk <= last - pd.Timedelta(weeks=4))]   # mature weeks only
    g = w.groupby(["district_id", "disease_group"])[["cases", "admissions"]].sum().reset_index()
    nat = w.groupby("disease_group")[["cases", "admissions"]].sum()
    nat_rate = (nat["admissions"] / nat["cases"].clip(lower=1)).to_dict()
    m = ASSUME["admission_rate_prior_strength"]
    g["adm_rate"] = [(r.admissions + m * nat_rate.get(r.disease_group, 0.1)) / (r.cases + m) for r in g.itertuples()]
    return g[["district_id", "disease_group", "adm_rate"]]


def demand_for(f: pd.DataFrame, rates: pd.DataFrame, stock: pd.DataFrame):
    """f: forecast rows of ONE origin (4 horizons, model nb_weather). Returns (demand, beds)."""
    f = f.merge(rates, on=["district_id", "disease_group"], how="left")
    f["adm_rate"] = f["adm_rate"].fillna(0.1)
    rows = []
    for (d, k), g in f.groupby(["district_id", "disease_group"]):
        mean_c, up_c, lo_c = g["mean"].sum(), g["q90"].sum(), g["q10"].sum()
        ar = g["adm_rate"].iloc[0]
        for r in RESOURCES:
            pa = ASSUME["per_admission"].get(r, {}).get(k)
            pc = ASSUME["per_case"].get(r, {}).get(k)
            if pa is None and pc is None:
                continue
            u = pa * ar if pa is not None else pc
            rows.append((d, r, k, mean_c * u, up_c * u, lo_c * u))
    dem = pd.DataFrame(rows, columns=["district_id", "resource", "disease_group", "mean", "upper", "lower"])
    dem = dem.groupby(["district_id", "resource"])[["mean", "upper", "lower"]].sum().reset_index()
    dem = dem.rename(columns={"mean": "demand_mean", "upper": "demand_upper", "lower": "demand_lower"})
    dem["stock"] = [float(stock.loc[d, r]) if d in stock.index else 0.0 for d, r in zip(dem["district_id"], dem["resource"])]
    dem["need_upper"] = (dem["demand_upper"] - dem["stock"]).clip(lower=0)
    dem["need_mean"] = (dem["demand_mean"] - dem["stock"]).clip(lower=0)
    los = ASSUME["length_of_stay_days"]
    f["beds_upper"] = f["q90"] * f["adm_rate"] * f["disease_group"].map(los) / 7.0
    f["beds_mean"] = f["mean"] * f["adm_rate"] * f["disease_group"].map(los) / 7.0
    bw = f.groupby(["district_id", "target_week"])[["beds_upper", "beds_mean"]].sum().reset_index()
    peak = bw.groupby("district_id")[["beds_upper", "beds_mean"]].max().reset_index()
    cap = stock[["bed_capacity", "occupied_beds"]].reset_index()
    beds = peak.merge(cap, on="district_id", how="left")
    beds["free_beds"] = (beds["bed_capacity"] - beds["occupied_beds"]).clip(lower=0)
    beds["bed_gap_upper"] = (beds["beds_upper"] - beds["free_beds"]).clip(lower=0)
    return dem, beds


def need_cost(dem):
    cost = {r: ASSUME["resources"][r]["cost_usd"] for r in RESOURCES}
    return float((dem["need_upper"] * dem["resource"].map(cost)).sum())


def build(origin="auto") -> dict:
    """origin: 'latest' (operational forecast), 'peak' (backtest origin with the largest national need),
    'auto' (latest unless its need is below 25% of the peak, which happens in the low season), or YYYY-MM-DD."""
    fc = read_table("gold", "forecasts")
    fc["origin_week"] = pd.to_datetime(fc["origin_week"])
    weekly = read_table("gold", "weekly_cases")
    stock_all = read_table("gold", "stock_district_week")
    stock_all["week_start"] = pd.to_datetime(stock_all["week_start"])
    fm = json.loads((config.OUTPUTS / "forecast_meta.json").read_text())
    latest_origin = pd.Timestamp(fm["op_origin"])

    def at(o, backtest):
        f = fc[(fc["model"] == "nb_weather") & (fc["origin_week"] == o) & (fc["is_backtest"] == backtest)]
        st = stock_all[stock_all["week_start"] == o].set_index("district_id")
        if st.empty or f.empty:
            return None
        return demand_for(f, admission_rates(weekly, o), st)

    cands = {}
    live = at(latest_origin, False)
    if live is not None:
        cands[latest_origin] = (live, need_cost(live[0]))
    bt_origins = sorted(fc[(fc["is_backtest"]) & (fc["model"] == "nb_weather")]["origin_week"].unique())
    bt = {}
    for o in bt_origins:
        r = at(pd.Timestamp(o), True)
        if r is not None:
            bt[pd.Timestamp(o)] = (r, need_cost(r[0]))
    peak_o = max(bt, key=lambda k: bt[k][1]) if bt else None
    latest_cost = cands[latest_origin][1] if latest_origin in cands else 0.0
    peak_cost = bt[peak_o][1] if peak_o is not None else 0.0
    if origin == "latest" or (origin == "auto" and (peak_o is None or latest_cost >= 0.25 * peak_cost)):
        chosen, (dem, beds), mode = latest_origin, cands[latest_origin][0], "latest"
    elif origin in ("peak", "auto"):
        chosen, (dem, beds), mode = peak_o, bt[peak_o][0], "peak_case_study"
    else:
        o = pd.Timestamp(origin)
        chosen, (dem, beds), mode = o, bt[o][0], "historical"
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    dem.to_csv(config.OUTPUTS / "demand.csv", index=False)
    beds.to_csv(config.OUTPUTS / "bed_demand.csv", index=False)
    admission_rates(weekly, chosen).to_csv(config.OUTPUTS / "admission_rates.csv", index=False)
    meta = dict(origin=str(chosen.date()), mode=mode, total_need_cost=need_cost(dem), latest_origin=str(latest_origin.date()),
                latest_total_need_cost=latest_cost, peak_origin=str(peak_o.date()) if peak_o is not None else None,
                peak_total_need_cost=peak_cost)
    (config.OUTPUTS / "demand_meta.json").write_text(json.dumps(meta, indent=1))
    return dict(demand_rows=len(dem), districts=dem["district_id"].nunique(), **meta)


if __name__ == "__main__":
    import sys
    print(build(sys.argv[1] if len(sys.argv) > 1 else "auto"))
