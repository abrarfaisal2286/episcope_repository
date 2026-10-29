"""Rolling-origin backtest evaluation (AN-5): MAE, 80% interval coverage and weighted interval score."""
import json
import numpy as np
import pandas as pd
from common import config
from common.io import read_table


def interval_score(l, u, y, alpha):
    return (u - l) + (2 / alpha) * np.maximum(l - y, 0) + (2 / alpha) * np.maximum(y - u, 0)


def wis(df):
    """WIS with a median and two central intervals (80% and 95%)."""
    m = np.abs(df["observed"] - df["q50"])
    is20 = interval_score(df["q10"], df["q90"], df["observed"], 0.2)
    is05 = interval_score(df["q025"], df["q975"], df["observed"], 0.05)
    K = 2
    return (0.5 * m + (0.2 / 2) * is20 + (0.05 / 2) * is05) / (K + 0.5)


def main():
    fc = read_table("gold", "forecasts")
    bt = fc[fc["is_backtest"] & fc["observed"].notna()].copy()
    if bt.empty:
        print("no backtest rows"); return
    bt["abs_err"] = (bt["observed"] - bt["mean"]).abs()
    bt["covered80"] = ((bt["observed"] >= bt["q10"]) & (bt["observed"] <= bt["q90"])).astype(float)
    bt["covered95"] = ((bt["observed"] >= bt["q025"]) & (bt["observed"] <= bt["q975"])).astype(float)
    bt["wis"] = wis(bt)
    keys = ["disease_group", "model", "horizon"]
    summ = bt.groupby(keys).agg(n=("abs_err", "size"), mae=("abs_err", "mean"), wis=("wis", "mean"),
                                coverage80=("covered80", "mean"), coverage95=("covered95", "mean"),
                                mean_observed=("observed", "mean")).reset_index()
    base = summ[summ["model"] == "seasonal_naive"].set_index(["disease_group", "horizon"])
    for col in ("mae", "wis"):
        summ[f"{col}_vs_naive"] = [1 - r[col] / base.loc[(r["disease_group"], r["horizon"]), col]
                                   if (r["disease_group"], r["horizon"]) in base.index else np.nan
                                   for _, r in summ.iterrows()]
    overall = bt.groupby(["disease_group", "model"]).agg(mae=("abs_err", "mean"), wis=("wis", "mean"),
                                                         coverage80=("covered80", "mean"),
                                                         coverage95=("covered95", "mean"),
                                                         mean_observed=("observed", "mean"), n=("abs_err", "size")).reset_index()
    # share of district series where the weather model beats the naive baseline (by MAE)
    per = bt.groupby(["district_id", "disease_group", "model"])["abs_err"].mean().unstack("model")
    skill = {}
    for dis, g in per.groupby(level="disease_group"):
        if "nb_weather" in g and "seasonal_naive" in g:
            skill[dis] = float((g["nb_weather"] < g["seasonal_naive"]).mean())
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    summ.to_csv(config.OUTPUTS / "forecast_backtest_by_horizon.csv", index=False)
    overall.to_csv(config.OUTPUTS / "forecast_backtest_overall.csv", index=False)
    (config.OUTPUTS / "forecast_backtest_summary.json").write_text(json.dumps(
        dict(origins=int(bt["origin_week"].nunique()), rows=int(len(bt)), share_districts_beating_naive=skill), indent=1))
    pd.set_option("display.width", 200)
    print(overall.round(3).to_string())
    print(summ[summ["model"] != "seasonal_naive"][["disease_group", "model", "horizon", "mae", "mae_vs_naive", "coverage80", "wis_vs_naive"]].round(3).to_string())
    print(skill)


if __name__ == "__main__":
    main()
