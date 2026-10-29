"""Per-district forecasting with lagged weather covariates, trained in parallel in Spark (AN-4, AN-5).

Model (interpretable, conflict C5): negative-binomial GLM with log link, one model per district x
disease x horizon (direct forecasting). Predictors use only data that is mature at the forecast origin
(weeks at least 4 weeks old, because reports arrive up to ~3 weeks late), plus weather lagged 4-8 weeks
before the target week, a short-term rainfall signal and annual harmonics.
Baselines: seasonal naive (same week last year). Ablation: the same GLM without weather.
Rolling-origin backtest over the last 52 forecastable origins; the model is refitted every 4 origins.
"""
import sys
import warnings
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import nbinom
from pyspark.sql import functions as F, types as T
from common import config
from common.spark_utils import get_spark

HORIZONS = [1, 2, 3, 4]
MATURE_GAP = 4                      # weeks until counts are (nearly) complete
N_BACKTEST_ORIGINS = 52
REFIT_EVERY = 4
QS = [0.025, 0.1, 0.5, 0.9, 0.975]
BACKTEST_DISEASES = ["dengue", "diarrhoea", "malaria"]      # the first two are the required ones

SCHEMA = T.StructType([
    T.StructField("district_id", T.StringType()), T.StructField("disease_group", T.StringType()),
    T.StructField("origin_week", T.DateType()), T.StructField("target_week", T.DateType()),
    T.StructField("horizon", T.IntegerType()), T.StructField("model", T.StringType()),
    T.StructField("mean", T.DoubleType()), T.StructField("q025", T.DoubleType()),
    T.StructField("q10", T.DoubleType()), T.StructField("q50", T.DoubleType()),
    T.StructField("q90", T.DoubleType()), T.StructField("q975", T.DoubleType()),
    T.StructField("observed", T.DoubleType()), T.StructField("is_backtest", T.BooleanType())])


def nb_quantiles(mu, alpha, qs=QS):
    mu = float(max(mu, 0.02)); n = 1.0 / alpha; p = n / (n + mu)
    return [float(nbinom.ppf(q, n, p)) for q in qs]


def _features(y, rain, temp, wk_dates, t, h, use_weather):
    """Feature vector for origin index t and horizon h (needs data up to t-4 for counts, up to t for weather)."""
    s = t + h
    m = t - MATURE_GAP
    short = np.log1p(np.nanmean(y[m - 1:m + 1]))
    long_ = np.log1p(np.nanmean(y[m - 7:m + 1]))
    wed = wk_dates.iloc[s] if s < len(wk_dates) else wk_dates.iloc[-1] + pd.Timedelta(weeks=s - len(wk_dates) + 1)
    doy = (wed + pd.Timedelta(days=3)).dayofyear / 365.25 * 2 * np.pi
    f = [1.0, short, long_, np.sin(doy), np.cos(doy), np.sin(2 * doy), np.cos(2 * doy)]
    if use_weather:
        rain_l = np.nanmean(rain[s - 8:s - 3]); temp_l = np.nanmean(temp[s - 8:s - 3])
        rain_r = np.log1p(np.nanmean(rain[t - 1:t + 1]))
        f += [rain_l, temp_l, rain_r]
    return np.array(f, float)


def _fit(X, y):
    """Poisson IRLS -> moment estimate of overdispersion -> NB IRLS. Returns (beta, alpha, mu_train)."""
    try:
        pois = sm.GLM(y, X, family=sm.families.Poisson()).fit(maxiter=40)
        mu = pois.mu
        alpha = float(np.clip(np.sum((y - mu) ** 2 - mu) / max(np.sum(mu ** 2), 1e-9), 0.02, 5.0))
        nbm = sm.GLM(y, X, family=sm.families.NegativeBinomial(alpha=alpha)).fit(maxiter=40)
        beta = nbm.params
        if not np.all(np.isfinite(beta)) or np.max(np.abs(beta)) > 25:
            raise ValueError("unstable")
        return beta, alpha
    except Exception:
        b = np.zeros(X.shape[1]); b[0] = np.log(max(y.mean(), 0.05))
        return b, 0.5


def _train_predict_models(y, rain, temp, wk, T_origin, h, use_weather, rows_cache):
    """Fit on pairs (t', t'+h) with target mature at T_origin; returns (beta, alpha, mu_x, sd_x)."""
    t_lo = max(MATURE_GAP + 8, 9)
    t_hi = T_origin - MATURE_GAP - h
    ts = list(range(t_lo + 8, t_hi + 1))
    if len(ts) < 40:
        return None
    X = np.array([_features(y, rain, temp, wk, t, h, use_weather) for t in ts])
    Y = np.array([y[t + h] for t in ts], float)
    ok = np.isfinite(X).all(1) & np.isfinite(Y)
    X, Y = X[ok], Y[ok]
    if len(Y) < 40:
        return None
    mu_x = X.mean(0); sd_x = X.std(0)
    mu_x[0], sd_x[0] = 0.0, 1.0
    sd_x[sd_x < 1e-9] = 1.0
    Xs = (X - mu_x) / sd_x
    if Y.mean() < 0.8:                              # very sparse series: intercept + harmonics only
        keep = [0, 3, 4]
        beta_k, alpha = _fit(Xs[:, keep], Y)
        beta = np.zeros(X.shape[1]); beta[keep] = beta_k
    else:
        beta, alpha = _fit(Xs, Y)
    return beta, alpha, mu_x, sd_x, float(Y.max())


def forecast_series(pdf: pd.DataFrame, mature_idx_date: str, op_origin_date: str, do_backtest: bool) -> pd.DataFrame:
    pdf = pdf.sort_values("week_start").reset_index(drop=True)
    did, dis = pdf["district_id"].iloc[0], pdf["disease_group"].iloc[0]
    wk = pd.to_datetime(pdf["week_start"]).reset_index(drop=True)
    y = pdf["cases"].to_numpy(float)
    rain = pdf["precip_mm"].to_numpy(float); temp = pdf["temp_c"].to_numpy(float)
    rain = pd.Series(rain).interpolate(limit_direction="both").to_numpy()
    temp = pd.Series(temp).interpolate(limit_direction="both").to_numpy()
    n = len(y)
    Lm = int(np.searchsorted(wk.values, np.datetime64(mature_idx_date), side="right") - 1)    # last mature index
    Top = int(np.searchsorted(wk.values, np.datetime64(op_origin_date), side="right") - 1)    # operational origin
    out = []

    def emit(model, t, h, mu, alpha, observed, is_bt):
        qs = nb_quantiles(mu, alpha)
        s = t + h
        target = wk.iloc[s] if s < n else wk.iloc[-1] + pd.Timedelta(weeks=s - n + 1)
        out.append((did, dis, wk.iloc[t].date(), target.date(), h, model, float(mu), *qs, observed, is_bt))

    variants = [("nb_weather", True), ("nb_noweather", False)]
    plan = []
    if do_backtest:
        origins = list(range(Lm - MATURE_GAP - N_BACKTEST_ORIGINS + 1, Lm - MATURE_GAP + 1))
        plan.append((origins, True))
    plan.append(([Top], False))
    for origins, is_bt in plan:
        for h in HORIZONS:
            for name, use_w in variants:
                if not is_bt and name == "nb_noweather":
                    continue
                fitted, last_fit = None, None
                alphas = []
                for k, t in enumerate(origins):
                    if fitted is None or (is_bt and k % REFIT_EVERY == 0):
                        fitted = _train_predict_models(y, rain, temp, wk, t, h, use_w, None)
                    s = t + h
                    obs = float(y[s]) if (is_bt and s < n) else None
                    if fitted is None:
                        continue
                    beta, alpha, mu_x, sd_x, ymax = fitted
                    x = (_features(y, rain, temp, wk, t, h, use_w) - mu_x) / sd_x
                    if len(beta) != len(x):                              # no-weather variant has fewer columns
                        x = x[:len(beta)]
                    mu = float(np.clip(np.exp(np.dot(beta[:len(x)], x)), 0.02, 3 * ymax + 10))
                    emit(name, t, h, mu, alpha, obs, is_bt)
                    alphas.append(alpha)
                    if name == "nb_weather":                              # baseline shares the fitted dispersion
                        if s - 52 >= 0:
                            base = max(float(y[s - 52]), 0.5)
                            emit("seasonal_naive", t, h, base, alpha, obs, is_bt)
    cols = [f.name for f in SCHEMA.fields]
    return pd.DataFrame(out, columns=cols)


def run(spark, do_backtest=True):
    cases = spark.read.parquet(str(config.GOLD / "weekly_cases")).where(F.col("disease_group").isin(config.DISEASES))
    wx = spark.read.parquet(str(config.GOLD / "weather_weekly")).select("district_id", "week_start", "precip_mm", "temp_c")
    sil = spark.read.parquet(str(config.SILVER / "encounters"))
    latest = sil.agg(F.max("report_date")).first()[0]
    # last complete report week and the last week whose counts are mature
    import datetime as dt
    from common.epi import week_start as _ws
    ws_latest = _ws(latest)
    last_full_week = ws_latest if ws_latest + dt.timedelta(days=6) <= latest else ws_latest - dt.timedelta(days=7)
    op_origin = last_full_week
    mature = op_origin - dt.timedelta(weeks=MATURE_GAP)
    d = (cases.where(F.col("week_start") <= F.lit(op_origin)).join(wx, ["district_id", "week_start"], "left")
         .select("district_id", "disease_group", "week_start", "cases", "precip_mm", "temp_c"))
    bt_set = set(BACKTEST_DISEASES)
    mature_s, op_s = mature.isoformat(), op_origin.isoformat()

    def udf(pdf):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return forecast_series(pdf, mature_s, op_s, pdf["disease_group"].iloc[0] in bt_set and do_backtest)

    # Resumable: districts are processed in parts; a part that finished is never recomputed after a restart.
    import shutil
    parts_dir = config.LAKE / "_stage" / "forecast_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    n_parts = 8
    ids = sorted(r[0] for r in d.select("district_id").distinct().collect())
    for k in range(n_parts):
        out_k = parts_dir / f"part_{k}"
        if (out_k / "_SUCCESS").exists():
            continue
        sel = ids[k::n_parts]
        res = d.where(F.col("district_id").isin(sel)).groupBy("district_id", "disease_group").applyInPandas(udf, SCHEMA)
        res.repartition(1).write.mode("overwrite").parquet(str(out_k))
        print(f"forecast part {k + 1}/{n_parts} done", flush=True)
    out = config.GOLD / "forecasts"
    shutil.rmtree(out, ignore_errors=True)
    spark.read.parquet(*[str(parts_dir / f"part_{k}") for k in range(n_parts)]).repartition(1).write.mode("overwrite").parquet(str(out))
    shutil.rmtree(parts_dir, ignore_errors=True)
    meta = dict(op_origin=op_s, mature_cutoff=mature_s, latest_report_date=str(latest))
    import json
    (config.OUTPUTS / "forecast_meta.json").write_text(json.dumps(meta))
    return spark.read.parquet(str(out)).count()


if __name__ == "__main__":
    spark = get_spark("forecast")
    print("forecast rows:", run(spark, do_backtest="--no-backtest" not in sys.argv))
    spark.stop()
