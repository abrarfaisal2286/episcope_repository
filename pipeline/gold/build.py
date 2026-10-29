"""Gold layer: epi-week marts, report-completeness, weather and stock aggregates, lineage (DQ-3).

Incremental: only weeks listed in the pending ledger (written by silver) are rebuilt, using dynamic
partition overwrite. Use --full to rebuild everything from silver.
"""
import datetime as dt
import json
import shutil
import sys
from pyspark.sql import functions as F, Window
from common import config
from common.epi import (week_start_col, epi_year_col, epi_week_col, epi_year_week)
from common.spark_utils import get_spark, read_ledger, write_ledger, register_tables

DISEASE_GROUPS = ["dengue", "diarrhoea", "ili", "malaria", "other"]
LATE_DAYS = 14
MIN_ACTIVE_MEAN = 3.0          # a facility counts as "usually reporting" above this weekly mean
TRAILING_WEEKS = 8


def _weeks_between(lo: dt.date, hi: dt.date):
    out, d = [], lo
    while d <= hi:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def _partition_filter(weeks):
    cond = None
    for w in weeks:
        y, wn = epi_year_week(w)
        c = (F.col("epi_year") == y) & (F.col("epi_week") == wn)
        cond = c if cond is None else (cond | c)
    return cond


def _read_silver_weeks(spark, weeks, col="week_start"):
    df = spark.read.parquet(str(config.SILVER / "encounters"))
    if 0 < len(weeks) <= 80:
        df = df.where(_partition_filter(weeks))
    return df.where(F.col(col).isin(weeks)) if len(weeks) <= 500 else df


def _write(df, name, part_cols):
    path = str(config.GOLD / name)
    w = df.repartition(*part_cols) if part_cols else df.coalesce(1)
    w = w.write.mode("overwrite")
    if part_cols:
        w = w.partitionBy(*part_cols)
    w.parquet(path)


def build(spark, full=False):
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    pend = read_ledger("gold_pending").get("weeks", [])
    sil_path = config.SILVER / "encounters"
    if not sil_path.exists():
        raise SystemExit("Silver encounters not found. Run the silver stage first.")
    gold_exists = (config.GOLD / "weekly_cases").exists()
    sil_all = spark.read.parquet(str(sil_path))
    if full or not gold_exists:
        W = sorted(r[0] for r in sil_all.select("week_start").distinct().collect())
    else:
        W = sorted(dt.date.fromisoformat(w) for w in pend)
    out = dict(weeks=len(W))
    districts = spark.read.parquet(str(config.SILVER / "districts"))
    registry = spark.read.parquet(str(config.SILVER / "registry"))
    if W:
        s = _read_silver_weeks(spark, W)
        clean = s.where(~F.col("is_duplicate_patient"))
        # ---- 1. reporting triangle: onset week x report week (what was known when)
        tri = (clean.groupBy("district_id", "disease_group", "week_start", "report_week_start")
               .agg(F.count("*").alias("cases"), F.sum("lab_confirmed").alias("lab_confirmed"),
                    F.sum("admitted").alias("admitted"),
                    F.sum(F.when(F.col("outcome") == "died", 1).otherwise(0)).alias("deaths"),
                    F.collect_set("_batch_id").alias("batch_ids"))
               .withColumn("epi_year", epi_year_col("week_start")).withColumn("epi_week", epi_week_col("week_start"))
               .persist())
        _write(tri, "cases_triangle", ["epi_year", "epi_week"])
        # ---- 2. weekly cases by onset week (dense grid, zero-filled)
        wk_df = spark.createDataFrame([(w,) for w in W], ["week_start"])
        dg_df = spark.createDataFrame([(g,) for g in DISEASE_GROUPS], ["disease_group"])
        grid = wk_df.crossJoin(districts.select("district_id", "division", "population")).crossJoin(dg_df)
        agg = (tri.groupBy("district_id", "disease_group", "week_start")
               .agg(F.sum("cases").alias("cases"), F.sum("lab_confirmed").alias("lab_confirmed"),
                    F.sum("admitted").alias("admissions"), F.sum("deaths").alias("deaths"),
                    F.array_sort(F.array_distinct(F.flatten(F.collect_list("batch_ids")))).alias("source_batch_ids")))
        wc = (grid.join(agg, ["district_id", "disease_group", "week_start"], "left")
              .withColumn("cases", F.coalesce("cases", F.lit(0)))
              .withColumn("lab_confirmed", F.coalesce("lab_confirmed", F.lit(0)))
              .withColumn("admissions", F.coalesce("admissions", F.lit(0)))
              .withColumn("deaths", F.coalesce("deaths", F.lit(0)))
              .withColumn("source_batch_ids", F.coalesce("source_batch_ids", F.array().cast("array<string>")))
              .withColumn("incidence_per_100k", F.round(F.col("cases") / F.col("population") * 100000, 3))
              .withColumn("epi_year", epi_year_col("week_start")).withColumn("epi_week", epi_week_col("week_start"))
              .withColumn("built_at", F.lit(now)))
        _write(wc, "weekly_cases", ["epi_year", "epi_week"])
        tri.unpersist()
        # ---- 3. report-week views (what a live surveillance system sees)
        R = sorted(r[0] for r in s.select("report_week_start").distinct().collect())
        lo = min(R) - dt.timedelta(days=70)
        src_weeks = _weeks_between(lo, max(R))
        rs = _read_silver_weeks(spark, src_weeks)
        rs = rs.where(F.col("report_week_start").isin(R))
        rep = (rs.where(~F.col("is_duplicate_patient"))
               .groupBy("district_id", "disease_group", "report_week_start")
               .agg(F.count("*").alias("n_reports"),
                    F.sum(F.when(F.col("report_delay_days") > LATE_DAYS, 1).otherwise(0)).alias("n_late"),
                    F.array_sort(F.collect_set("_batch_id")).alias("source_batch_ids")))
        rw_df = spark.createDataFrame([(w,) for w in R], ["report_week_start"])
        rgrid = rw_df.crossJoin(districts.select("district_id")).crossJoin(dg_df)
        wr = (rgrid.join(rep, ["district_id", "disease_group", "report_week_start"], "left")
              .withColumn("n_reports", F.coalesce("n_reports", F.lit(0)))
              .withColumn("n_late", F.coalesce("n_late", F.lit(0)))
              .withColumn("source_batch_ids", F.coalesce("source_batch_ids", F.array().cast("array<string>")))
              .withColumn("epi_year", epi_year_col("report_week_start"))
              .withColumn("epi_week", epi_week_col("report_week_start")))
        _write(wr, "weekly_reports", ["epi_year", "epi_week"])
        # ---- 4. facility weekly volume and district completeness
        fac_cnt = (rs.groupBy("facility_id", "report_week_start").agg(F.count("*").alias("n_all")))
        fgrid = rw_df.crossJoin(registry.select("facility_id", "district_id"))
        fw_new = (fgrid.join(fac_cnt, ["facility_id", "report_week_start"], "left")
                  .withColumn("n_all", F.coalesce("n_all", F.lit(0))))
        prior_path = config.GOLD / "facility_weekly"
        fw_all = fw_new
        if prior_path.exists() and not full:
            prior = (spark.read.parquet(str(prior_path))
                     .where((F.col("report_week_start") >= F.lit(min(R) - dt.timedelta(days=7 * TRAILING_WEEKS)))
                            & (F.col("report_week_start") < F.lit(min(R))))
                     .select("facility_id", "district_id", "report_week_start", "n_all"))
            fw_all = prior.unionByName(fw_new)
        wf = Window.partitionBy("facility_id").orderBy("report_week_start").rowsBetween(-TRAILING_WEEKS, -1)
        fw = (fw_all.withColumn("trailing_mean", F.avg("n_all").over(wf))
              .where(F.col("report_week_start").isin(R))
              .withColumn("active", F.coalesce(F.col("trailing_mean") >= MIN_ACTIVE_MEAN, F.lit(False)))
              .withColumn("silent", F.col("active") & (F.col("n_all") == 0))
              .withColumn("epi_year", epi_year_col("report_week_start"))
              .withColumn("epi_week", epi_week_col("report_week_start")).persist())
        stage = config.LAKE / "_stage" / "facility_weekly"          # never overwrite a path that is being read
        shutil.rmtree(stage, ignore_errors=True)
        fw.select("facility_id", "district_id", "report_week_start", "n_all", "epi_year", "epi_week") \
            .repartition("epi_year", "epi_week").write.mode("overwrite").parquet(str(stage))
        spark.read.parquet(str(stage)).write.mode("overwrite").partitionBy("epi_year", "epi_week").parquet(str(prior_path))
        shutil.rmtree(stage, ignore_errors=True)
        comp = (fw.groupBy("district_id", "report_week_start").agg(
                    F.sum(F.when(F.col("active"), F.col("trailing_mean")).otherwise(0.0)).alias("expected_volume"),
                    F.sum(F.when(F.col("silent"), F.col("trailing_mean")).otherwise(0.0)).alias("silent_volume"),
                    F.sum(F.col("silent").cast("int")).alias("facilities_silent"),
                    F.sum(F.col("active").cast("int")).alias("facilities_active"))
                .join(rs.groupBy("district_id", "report_week_start").agg(
                    F.count("*").alias("n_reports_all"),
                    F.sum(F.when(F.col("report_delay_days") > LATE_DAYS, 1).otherwise(0)).alias("n_late_all")),
                    ["district_id", "report_week_start"], "left")
                .withColumn("n_reports_all", F.coalesce("n_reports_all", F.lit(0)))
                .withColumn("n_late_all", F.coalesce("n_late_all", F.lit(0)))
                .withColumn("completeness", F.round(F.when(F.col("expected_volume") > 0,
                            1 - F.col("silent_volume") / F.col("expected_volume")).otherwise(1.0), 4))
                .withColumn("late_share", F.round(F.when(F.col("n_reports_all") > 0,
                            F.col("n_late_all") / F.col("n_reports_all")).otherwise(0.0), 4))
                .withColumn("epi_year", epi_year_col("report_week_start"))
                .withColumn("epi_week", epi_week_col("report_week_start")))
        _write(comp, "report_completeness", ["epi_year", "epi_week"])
        fw.unpersist()
    # ---- 5. weather (weekly) and stock (district, end of week): small, rebuilt in full
    wx = spark.read.parquet(str(config.SILVER / "weather"))
    wxw = (wx.withColumn("week_start", week_start_col("date")).groupBy("district_id", "week_start")
           .agg(F.round(F.sum("precip_mm"), 1).alias("precip_mm"), F.round(F.avg("temp_c"), 2).alias("temp_c"),
                F.round(F.avg("humidity_pct"), 1).alias("humidity_pct"), F.count("*").alias("n_days"))
           .withColumn("epi_year", epi_year_col("week_start")).withColumn("epi_week", epi_week_col("week_start")))
    wxw.repartition("epi_year").write.mode("overwrite").partitionBy("epi_year").parquet(str(config.GOLD / "weather_weekly"))
    st = (spark.read.parquet(str(config.SILVER / "stock")).join(registry.select("facility_id", "district_id", "beds"),
          "facility_id").withColumn("week_start", week_start_col("date")))
    last = st.groupBy("week_start").agg(F.max("date").alias("last_date"))
    sd = (st.join(last, "week_start").where(F.col("date") == F.col("last_date"))
          .groupBy("district_id", "week_start", "last_date")
          .agg(F.sum("iv_saline").alias("iv_saline"), F.sum("ns1_kits").alias("ns1_kits"), F.sum("ors").alias("ors"),
               F.sum("antimalarials").alias("antimalarials"), F.sum("occupied_beds").alias("occupied_beds"),
               F.sum("beds").alias("bed_capacity")))
    sd.coalesce(1).write.mode("overwrite").parquet(str(config.GOLD / "stock_district_week"))
    latest = spark.read.parquet(str(sil_path)).agg(F.max("report_date")).first()[0]
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    (config.OUTPUTS / "pipeline_meta.json").write_text(json.dumps(
        dict(built_at=now, latest_report_date=str(latest), weeks_rebuilt=len(W))))
    write_ledger("gold_pending", {"weeks": []})
    (config.OUTPUTS / "last_gold_run.json").write_text(json.dumps(dict(weeks_rebuilt=len(W), built_at=now)))
    out["latest_report_date"] = str(latest)
    return out


def main():
    full = "--full" in sys.argv
    spark = get_spark("gold")
    print(build(spark, full))
    print("registered:", register_tables(spark))
    spark.stop()


if __name__ == "__main__":
    main()
