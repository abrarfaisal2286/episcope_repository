"""Silver layer: typed, validated, de-identified, de-duplicated data (DQ-1, DQ-4, PRV-1, ING-3, ING-4).

Incremental and idempotent: only bronze batches absent from the silver ledger are processed.
Late records update the epi-weeks they belong to: affected partitions (+/- 1 week, for patient
de-duplication across week boundaries) are rebuilt with dynamic partition overwrite.
"""
import datetime as dt
import shutil
import sys
from pyspark.sql import functions as F, Window
from common import config
from common.epi import (week_start_col, epi_year_col, epi_week_col, week_start, epi_year_week)
from common.spark_utils import get_spark, read_ledger, write_ledger
from pipeline.quality import engine
from pipeline.quality.rules import encounter_rules

ENC_RULES = ["R01_NULL_ENCOUNTER_ID", "R02_NULL_PATIENT_ID", "R03_NULL_FACILITY", "R04_BAD_ONSET_DATE",
             "R05_BAD_REPORT_DATE", "R06_ONSET_AFTER_REPORT", "R07_DELAY_TOO_LONG", "R08_IMPLAUSIBLE_DATE",
             "R09_BAD_AGE", "R10_BAD_SEX", "R11_BAD_ICD10", "R12_BAD_FLAG", "R13_BAD_OUTCOME",
             "R14_ORPHAN_FACILITY", "R15_INVALID_GEOGRAPHY", "R16_DUPLICATE_IN_BATCH",
             "R17_DUPLICATE_EXISTING"]
ICD_MAP = config.ICD_TO_DISEASE
_now = lambda: dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _latest_batch(spark, source):
    b = spark.read.parquet(str(config.BRONZE / source))
    latest = b.agg(F.max("_load_ts")).first()[0]
    return b.where(F.col("_load_ts") == latest)


def build_reference(spark):
    """Registry and districts: replaced by the latest bronze version (small, full refresh)."""
    d = _latest_batch(spark, "districts").select(
        "district_id", "district", "division", F.col("lat").cast("double"), F.col("lon").cast("double"),
        F.col("population").cast("long"), F.col("urban").cast("double"), F.col("hill").cast("double"),
        F.col("flood").cast("double"), F.col("rain_factor").cast("double"), "_batch_id")
    d.write.mode("overwrite").parquet(str(config.SILVER / "districts"))
    r = _latest_batch(spark, "registry").select(
        "facility_id", "district_id", "facility_type", F.col("beds").cast("int"),
        F.col("icu_beds").cast("int"), F.col("lat").cast("double"), F.col("lon").cast("double"), "_batch_id")
    r.write.mode("overwrite").parquet(str(config.SILVER / "registry"))


def _merge_partitions(spark, new_df, path, part_cols, keys, order_col):
    """Merge new rows into existing partitions (latest order_col wins per key); safe overwrite via stage."""
    parts = [tuple(r) for r in new_df.select(*part_cols).distinct().collect()]
    if (path).exists() and parts:
        cond = None
        for p in parts:
            c = None
            for col, v in zip(part_cols, p):
                e = F.col(col) == F.lit(v)
                c = e if c is None else (c & e)
            cond = c if cond is None else (cond | c)
        existing = spark.read.parquet(str(path)).where(cond)
        new_df = existing.unionByName(new_df)
    w = Window.partitionBy(*keys).orderBy(F.col(order_col).desc())
    merged = new_df.withColumn("_rn", F.row_number().over(w)).where("_rn = 1").drop("_rn")
    stage = config.LAKE / "_stage" / path.name
    shutil.rmtree(stage, ignore_errors=True)
    merged.repartition(*part_cols).write.mode("overwrite").parquet(str(stage))
    spark.read.parquet(str(stage)).write.mode("overwrite").partitionBy(*part_cols).parquet(str(path))
    shutil.rmtree(stage, ignore_errors=True)


def _append_dq(spark, dq_df):
    out = config.GOLD / "dq_report"
    dq_df.write.mode("append").parquet(str(out))


def _quarantine(invalid_df):
    (invalid_df.write.mode("append").partitionBy("source_month")
     .parquet(str(config.SILVER / "quarantine")))


def _new_batches(source):
    bl = read_ledger(f"bronze_{source}")
    sl = read_ledger(f"silver_{source}")
    items = [v for v in bl.values() if v["batch_id"] not in sl]
    return items, sl


def build_weather(spark):
    items, sl = _new_batches("weather")
    if not items:
        return 0
    ids = [v["batch_id"] for v in items]
    b = spark.read.parquet(str(config.BRONZE / "weather")).where(F.col("_batch_id").isin(ids))
    dist = spark.read.parquet(str(config.SILVER / "districts")).select(F.col("district_id").alias("_gd"))
    p = (b.withColumn("date", F.to_date("date", "yyyy-MM-dd"))
         .withColumn("precip_mm", F.col("precip_mm").cast("double"))
         .withColumn("temp_c", F.col("temp_c").cast("double"))
         .withColumn("humidity_pct", F.col("humidity_pct").cast("double"))
         .join(F.broadcast(dist), F.col("district_id") == F.col("_gd"), "left"))
    rules = [("W01_NULL_KEY", F.col("date").isNull() | F.col("district_id").isNull()),
             ("W02_NEGATIVE_RAIN", F.col("precip_mm") < 0), ("W03_BAD_TEMP", ~F.col("temp_c").between(-5, 50)),
             ("W04_BAD_HUMIDITY", ~F.col("humidity_pct").between(0, 100)),
             ("W05_UNKNOWN_DISTRICT", F.col("_gd").isNull())]
    d = engine.evaluate(p, rules)
    valid, invalid = engine.split(d)
    valid = (valid.select("date", "district_id", "precip_mm", "temp_c", "humidity_pct", "source",
                          "_batch_id", "_source_date").withColumn("year", F.year("date")))
    _merge_partitions(spark, valid, config.SILVER / "weather", ["year"], ["date", "district_id"], "_source_date")
    _append_dq(spark, engine.dq_rows(spark, d, "weather", [r[0] for r in rules]))
    d.unpersist()
    for v in items:
        sl[v["batch_id"]] = dict(done_at=_now())
    write_ledger("silver_weather", sl)
    return len(items)


STOCK_ALIASES = {
    "date": ["date", "snapshot_date", "snap_date", "report_date"],
    "facility_id": ["facility_id", "facility", "fac_id"],
    "iv_saline": ["iv_saline", "iv_saline_bags", "ivsaline", "saline"],
    "ns1_kits": ["ns1_kits", "ns1", "ns1_test_kits"],
    "ors": ["ors", "ors_sachets"],
    "antimalarials": ["antimalarials", "antimalarial", "antimalarial_courses"],
    "occupied_beds": ["occupied_beds", "beds_occupied", "bed_occupancy", "occupied"],
}


def resolve_columns(df, aliases):
    """Map known column-name variants to the contract names; missing columns become null (then quarantined)."""
    have = set(df.columns)
    cols = []
    for k, al in aliases.items():
        hit = next((a for a in al if a in have), None)
        cols.append(F.col(hit).alias(k) if hit else F.lit(None).cast("string").alias(k))
    return df.select(*cols, "_batch_id", "_source_date")


def build_stock(spark):
    items, sl = _new_batches("stock")
    if not items:
        return 0
    ids = [v["batch_id"] for v in items]
    b = resolve_columns(spark.read.parquet(str(config.BRONZE / "stock")).where(F.col("_batch_id").isin(ids)), STOCK_ALIASES)
    reg = spark.read.parquet(str(config.SILVER / "registry")).select("facility_id", "beds")
    p = b.withColumn("date", F.to_date("date", "yyyy-MM-dd"))
    for c in ["iv_saline", "ns1_kits", "ors", "antimalarials", "occupied_beds"]:
        p = p.withColumn(c, F.col(c).cast("long"))
    p = p.join(F.broadcast(reg), "facility_id", "left")
    neg = (F.col("iv_saline") < 0) | (F.col("ns1_kits") < 0) | (F.col("ors") < 0) | \
          (F.col("antimalarials") < 0) | (F.col("occupied_beds") < 0)
    rules = [("S01_NULL_KEY", F.col("date").isNull() | F.col("facility_id").isNull()),
             ("S02_NEGATIVE_STOCK", neg), ("S03_UNKNOWN_FACILITY", F.col("beds").isNull()),
             ("S04_OCCUPANCY_OVER_CAPACITY", F.col("occupied_beds") > F.col("beds"))]
    d = engine.evaluate(p, rules)
    valid, invalid = engine.split(d)
    valid = (valid.select("date", "facility_id", "iv_saline", "ns1_kits", "ors", "antimalarials",
                          "occupied_beds", "_batch_id", "_source_date").withColumn("snap_year", F.year("date")))
    _merge_partitions(spark, valid, config.SILVER / "stock", ["snap_year"], ["date", "facility_id"], "_source_date")
    _append_dq(spark, engine.dq_rows(spark, d, "stock", [r[0] for r in rules]))
    d.unpersist()
    for v in items:
        sl[v["batch_id"]] = dict(done_at=_now())
    write_ledger("silver_stock", sl)
    return len(items)


def _ext_weeks(week_starts):
    ext = set()
    for ws in week_starts:
        for k in (-7, 0, 7):
            ext.add(ws + dt.timedelta(days=k))
    return sorted(ext)


def build_encounters(spark):
    items, sl = _new_batches("encounters")
    if not items:
        return dict(batches=0)
    if not config.SALT:
        raise SystemExit("EPISCOPE_SALT is not set. Copy .env.example to .env and set a secret salt.")
    ids = [v["batch_id"] for v in items]
    months = sorted({v["source_date"][:7] for v in items})
    b = (spark.read.parquet(str(config.BRONZE / "encounters"))
         .where(F.col("source_month").isin(months)).where(F.col("_batch_id").isin(ids)))
    reg = spark.read.parquet(str(config.SILVER / "registry")).select(
        "facility_id", F.col("district_id").alias("reg_district_id"))
    dist = spark.read.parquet(str(config.SILVER / "districts")).select(
        F.col("district_id").alias("geo_district_id"), "division")
    p = (b.withColumn("onset", F.to_date("onset_date", "yyyy-MM-dd"))
         .withColumn("report", F.to_date("report_date", "yyyy-MM-dd"))
         .withColumn("age", F.col("age_years").cast("int"))
         .withColumn("lab", F.col("lab_confirmed").cast("int")).withColumn("adm", F.col("admitted").cast("int"))
         .join(F.broadcast(reg), "facility_id", "left")
         .join(F.broadcast(dist), F.col("reg_district_id") == F.col("geo_district_id"), "left"))
    rules = encounter_rules()
    d = engine.evaluate(p, rules)
    valid, invalid = engine.split(d)
    # --- duplicates inside the new batches (R16): keep the earliest submission
    w = Window.partitionBy("encounter_id").orderBy("_source_date", "_batch_id")
    ranked = valid.withColumn("_rn", F.row_number().over(w)).persist()
    dup_in = ranked.where("_rn > 1")
    clean = ranked.where("_rn = 1").drop("_rn")
    # --- de-identification and derived keys
    salted = F.sha2(F.concat(F.lit(config.SALT), F.col("patient_id")), 256)
    icd3 = F.substring("icd10_code", 1, 3)
    dg = F.create_map(*[x for kv in ICD_MAP.items() for x in (F.lit(kv[0]), F.lit(kv[1]))])[icd3]
    ws = week_start_col(F.col("onset"))
    new = clean.select(
        "encounter_id", salted.alias("patient_hash"), "facility_id",
        F.col("reg_district_id").alias("district_id"), "division",
        F.col("onset").alias("onset_date"), F.col("report").alias("report_date"),
        F.when(F.col("age") < 5, "0-4").when(F.col("age") < 15, "5-14").when(F.col("age") < 25, "15-24")
        .when(F.col("age") < 45, "25-44").when(F.col("age") < 65, "45-64").otherwise("65+").alias("age_band"),
        "sex", "icd10_code", F.coalesce(dg, F.lit("other")).alias("disease_group"),
        F.col("lab").alias("lab_confirmed"), F.col("adm").alias("admitted"), "outcome",
        ws.alias("week_start"), week_start_col(F.col("report")).alias("report_week_start"),
        F.datediff("report", "onset").alias("report_delay_days"),
        "_batch_id", "_source_date")
    new = (new.withColumn("epi_year", epi_year_col("week_start")).withColumn("epi_week", epi_week_col("week_start"))
           .withColumn("is_duplicate_patient", F.lit(False)).persist())
    wk = [r[0] for r in new.select("week_start").distinct().collect()]
    ext = _ext_weeks(wk)
    # --- existing silver partitions for the affected weeks (late data updates them)
    silver_path = config.SILVER / "encounters"
    existing = None
    if silver_path.exists():
        cond = None
        for e in ext:
            y, wn = epi_year_week(e)
            c = (F.col("epi_year") == y) & (F.col("epi_week") == wn)
            cond = c if cond is None else (cond | c)
        existing = spark.read.parquet(str(silver_path)).where(cond)
        ex_ids = existing.select("encounter_id").dropDuplicates().withColumn("_exists", F.lit(1))
        j = new.join(ex_ids, "encounter_id", "left")
        dup_ex = j.where("_exists = 1").select("encounter_id", "_batch_id")
        new = j.where("_exists is null").drop("_exists")
    else:
        dup_ex = None
    # --- cross-facility patient de-duplication (DQ-4)
    comb = new if existing is None else existing.unionByName(new)
    wp = Window.partitionBy("patient_hash", "disease_group").orderBy("onset_date", "encounter_id")
    prev = lambda c: F.lag(c).over(wp)
    flag = (prev("onset_date").isNotNull()
            & (F.datediff("onset_date", prev("onset_date")).between(0, 7))
            & (prev("facility_id") != F.col("facility_id"))
            & (prev("age_band") == F.col("age_band")) & (prev("sex") == F.col("sex"))
            & (F.col("disease_group") != "other"))
    comb = comb.withColumn("is_duplicate_patient", F.coalesce(flag, F.lit(False)))
    # --- quarantine with reason codes (R16/R17 included)
    q_cols = [c for c in b.columns]
    q_valid = invalid.select(*q_cols, "_reason_codes")
    q_dup_in = dup_in.select(*q_cols).withColumn("_reason_codes", F.lit("R16_DUPLICATE_IN_BATCH"))
    q_all = q_valid.unionByName(q_dup_in)
    if dup_ex is not None:
        q_ex = (b.join(dup_ex.select("encounter_id", F.col("_batch_id").alias("_bb")), "encounter_id")
                .where(F.col("_batch_id") == F.col("_bb")).drop("_bb")
                .withColumn("_reason_codes", F.lit("R17_DUPLICATE_EXISTING")))
        q_all = q_all.unionByName(q_ex.select(*q_cols, "_reason_codes"))
    _quarantine(q_all)
    # --- quality report (DQ-2)
    extra = dup_in.groupBy("_batch_id").count().withColumn("rule_code", F.lit("R16_DUPLICATE_IN_BATCH")) \
        .withColumnRenamed("count", "rule_fail_count")
    if dup_ex is not None:
        extra = extra.unionByName(dup_ex.groupBy("_batch_id").count()
                                  .withColumn("rule_code", F.lit("R17_DUPLICATE_EXISTING"))
                                  .withColumnRenamed("count", "rule_fail_count"))
    dq = engine.dq_rows(spark, d, "encounters", ENC_RULES, extra_fail=extra)
    _append_dq(spark, dq.drop("rows_failed_validity"))
    n_valid = new.count()
    stage = config.LAKE / "_stage" / "encounters"
    shutil.rmtree(stage, ignore_errors=True)
    comb.repartition("epi_year", "epi_week").write.mode("overwrite").parquet(str(stage))
    spark.read.parquet(str(stage)).write.mode("overwrite").partitionBy("epi_year", "epi_week").parquet(str(silver_path))
    shutil.rmtree(stage, ignore_errors=True)
    ranked.unpersist(); new.unpersist(); d.unpersist()
    for v in items:
        sl[v["batch_id"]] = dict(done_at=_now())
    write_ledger("silver_encounters", sl)
    pend = read_ledger("gold_pending")                   # tells gold which weeks must be rebuilt
    weeks = set(pend.get("weeks", [])) | {e.isoformat() for e in ext}
    write_ledger("gold_pending", {"weeks": sorted(weeks)})
    return dict(batches=len(items), new_valid_rows=n_valid, weeks_rebuilt=len(ext))


def main():
    spark = get_spark("silver")
    build_reference(spark)
    print("weather batches:", build_weather(spark))
    print("stock batches:", build_stock(spark))
    print("encounters:", build_encounters(spark))
    spark.stop()


if __name__ == "__main__":
    main()
