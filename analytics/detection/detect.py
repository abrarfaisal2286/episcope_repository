"""Outbreak detection at scale (AN-1, AN-3): EARS C2, a Farrington-style seasonal detector and CUSUM per district x disease in Spark.

Input : gold weekly_reports (cases by REPORT week, what a live system sees) and report_completeness.
Output: gold detection_scores (one row per district, disease, report week) with both method scores,
        an artefact flag, and the baseline used. Nothing here reads ground truth.

Artefact guard: a week is flagged as a reporting artefact when the share of late reports in the
district (delay > 14 days) is high, which is the signature of a facility back-filling after silence.
Flagged weeks are excluded from the baselines and cannot raise alerts.
"""
import sys
import numpy as np
import pandas as pd
from pyspark.sql import functions as F, types as T
from common import config
from common.spark_utils import get_spark

BASELINE_WEEKS = 8
GUARD_WEEKS = 2
ARTEFACT_LATE_SHARE = 0.15
ARTEFACT_MIN_LATE = 5
CUSUM_K = 1.0
CUSUM_DECAY = 0.8        # decaying CUSUM: old evidence fades, so scores fall back after an outbreak

SCHEMA = T.StructType([
    T.StructField("district_id", T.StringType()), T.StructField("disease_group", T.StringType()),
    T.StructField("report_week_start", T.DateType()), T.StructField("cases", T.IntegerType()),
    T.StructField("baseline_mean", T.DoubleType()), T.StructField("baseline_sd", T.DoubleType()),
    T.StructField("z_c2", T.DoubleType()), T.StructField("cusum", T.DoubleType()),
    T.StructField("artefact_flag", T.BooleanType()), T.StructField("naive_z_c2", T.DoubleType()),
    T.StructField("baseline_mean_fl", T.DoubleType()), T.StructField("baseline_sd_fl", T.DoubleType()),
    T.StructField("z_fl", T.DoubleType()),
])


def score_series(pdf: pd.DataFrame, use_artefact_guard: bool = True) -> pd.DataFrame:
    """Scores for one district x disease series (sorted by week)."""
    pdf = pdf.sort_values("report_week_start").reset_index(drop=True)
    x = pdf["cases"].to_numpy(float)
    art = pdf["artefact_flag"].to_numpy(bool)
    xb = np.where(art, np.nan, x) if use_artefact_guard else x.copy()   # baseline ignores artefact weeks
    n = len(x)
    mu = np.full(n, np.nan); sd = np.full(n, np.nan)
    z = np.full(n, np.nan); naive = np.full(n, np.nan)
    for t in range(n):
        lo, hi = t - GUARD_WEEKS - BASELINE_WEEKS + 1, t - GUARD_WEEKS
        if lo < 0:
            continue
        base = xb[lo:hi + 1]
        base = base[~np.isnan(base)]
        if len(base) < 5:
            continue
        m = base.mean()
        s = max(base.std(ddof=1), np.sqrt(max(m, 1.0)))              # at least Poisson variation
        mu[t], sd[t] = m, s
        z[t] = (x[t] - m) / s
        nb = x[lo:hi + 1]
        naive[t] = (x[t] - nb.mean()) / max(nb.std(ddof=1), np.sqrt(max(nb.mean(), 1.0)))
    # Farrington-style seasonal baseline: same weeks (+/- 3) of up to 3 previous years, trimmed, scaled by the
    # recent year-to-year level ratio, with a quasi-Poisson dispersion estimated from the reference weeks.
    mu_f = np.full(n, np.nan); sd_f = np.full(n, np.nan); zf = np.full(n, np.nan)
    for t in range(n):
        refs = []
        for k in (1, 2, 3):
            c = t - 52 * k
            if c - 3 < 0:
                break
            refs.extend(xb[c - 3:c + 4])
        refs = np.array(refs, float); refs = refs[~np.isnan(refs)]
        if len(refs) < 7:
            continue
        srt = np.sort(refs); keep = srt[:max(5, int(0.8 * len(srt)))]
        mu_s = keep.mean(); phi = max(1.0, keep.var(ddof=1) / max(mu_s, 1.0))
        cur = xb[t - 9:t - 1] if t >= 9 else np.array([np.nan])
        ratios = []
        for k in (1, 2, 3):
            lo = t - 9 - 52 * k
            if lo >= 0:
                ratios.append(np.nanmean(xb[lo:lo + 8]) if not np.all(np.isnan(xb[lo:lo + 8])) else np.nan)
        refc = np.nanmean(ratios) if ratios and not np.all(np.isnan(ratios)) else np.nan
        curm = np.nanmean(cur) if not np.all(np.isnan(cur)) else np.nan
        r = float(np.clip((curm + 1) / (refc + 1), 0.5, 2.0)) if not (np.isnan(curm) or np.isnan(refc)) else 1.0
        m = mu_s * r
        mu_f[t], sd_f[t] = m, np.sqrt(phi * max(m, 1.0))
        zf[t] = (x[t] - m) / sd_f[t]
    cus = np.zeros(n); s_prev = 0.0
    for t in range(n):
        zt = 0.0 if np.isnan(zf[t]) else float(np.clip(zf[t], -3, 6))
        if use_artefact_guard and art[t]:
            zt = 0.0
        s_prev = max(0.0, CUSUM_DECAY * s_prev + zt - CUSUM_K)
        cus[t] = s_prev
    out = pd.DataFrame({
        "district_id": pdf["district_id"], "disease_group": pdf["disease_group"],
        "report_week_start": pdf["report_week_start"], "cases": pdf["cases"].astype(int),
        "baseline_mean": mu, "baseline_sd": sd, "z_c2": z, "cusum": cus,
        "artefact_flag": art, "naive_z_c2": naive,
        "baseline_mean_fl": mu_f, "baseline_sd_fl": sd_f, "z_fl": zf})
    return out


def _udf(pdf: pd.DataFrame) -> pd.DataFrame:
    return score_series(pdf)


def run(spark):
    wr = spark.read.parquet(str(config.GOLD / "weekly_reports"))
    comp = spark.read.parquet(str(config.GOLD / "report_completeness")).select(
        "district_id", "report_week_start", "late_share", "n_late_all", "completeness", "n_reports_all")
    d = (wr.where(F.col("disease_group").isin(config.DISEASES))
         .select("district_id", "disease_group", "report_week_start", F.col("n_reports").alias("cases"))
         .join(comp, ["district_id", "report_week_start"], "left")
         .withColumn("artefact_flag", F.coalesce((F.col("late_share") >= ARTEFACT_LATE_SHARE) & (F.col("n_late_all") >= ARTEFACT_MIN_LATE), F.lit(False)))
         .select("district_id", "disease_group", "report_week_start", "cases", "artefact_flag"))
    # the final report week is incomplete until it has ended: score completed weeks only
    latest = spark.read.parquet(str(config.SILVER / "encounters")).agg(F.max("report_date")).first()[0]
    d = d.where(F.date_add("report_week_start", 6) <= F.lit(latest))
    scores = d.groupBy("district_id", "disease_group").applyInPandas(_udf, SCHEMA)
    out = config.GOLD / "detection_scores"
    scores.repartition(1).write.mode("overwrite").parquet(str(out))
    return spark.read.parquet(str(out)).count()


if __name__ == "__main__":
    spark = get_spark("detect")
    print("score rows:", run(spark))
    spark.stop()
