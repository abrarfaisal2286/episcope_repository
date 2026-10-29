"""ENG-3: Spark optimisations with before/after evidence on the real lake.

Experiments (each timed 3 times, median reported; stage metrics pulled from the Spark REST API):
  1. Broadcast join of the 650-row facility registry vs a forced sort-merge join (shuffle both sides).
  2. Partition pruning: one epi-week from silver partitioned by (epi_year, epi_week) vs an unpartitioned copy.
  3. Skew: aggregation by district with Dhaka-heavy keys, plain vs salted two-stage aggregation, and AQE on/off.
  4. File compaction: many small files (one per original write task) vs compacted files for a time-window scan.
Results: docs/spark_optimisations.csv and docs/spark_optimisations.json. Run: python scripts/spark_optimisations.py
"""
import json
import pathlib
import shutil
import statistics
import time
import urllib.request
from pyspark.sql import functions as F
from common import config
from common.spark_utils import get_spark

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs"


def timed(fn, reps=3):
    ts = []
    for _ in range(reps):
        t0 = time.time(); r = fn(); ts.append(time.time() - t0)
    return round(statistics.median(ts), 3), r


def stage_summary(spark):
    """Totals from the Spark REST API for the application so far."""
    try:
        base = f"http://localhost:{spark.sparkContext.uiWebUrl.split(':')[-1]}/api/v1/applications/{spark.sparkContext.applicationId}"
        st = json.load(urllib.request.urlopen(base + "/stages?status=complete", timeout=10))
        return dict(stages=len(st), shuffle_read_mb=round(sum(s["shuffleReadBytes"] for s in st) / 1e6, 2),
                    shuffle_write_mb=round(sum(s["shuffleWriteBytes"] for s in st) / 1e6, 2),
                    input_mb=round(sum(s["inputBytes"] for s in st) / 1e6, 2), tasks=sum(s["numCompleteTasks"] for s in st))
    except Exception as exc:
        return dict(error=str(exc)[:80])


def delta(a, b):
    return dict(before_s=a, after_s=b, speedup=round(a / b, 2) if b else None)


def main():
    spark = get_spark("optimisations")
    spark.conf.set("spark.sql.adaptive.enabled", "true")
    results = []
    sil = config.SILVER / "encounters"
    reg = spark.read.parquet(str(config.SILVER / "registry"))
    enc = spark.read.parquet(str(sil)).select("encounter_id", "facility_id", "district_id", "week_start", "epi_year", "epi_week", "disease_group")
    n = enc.count()

    # 1. broadcast join vs sort-merge join -------------------------------------------------------------
    def join_count(broadcast):
        spark.conf.set("spark.sql.autoBroadcastJoinThreshold", "10485760" if broadcast else "-1")
        spark.conf.set("spark.sql.adaptive.enabled", "false")
        r = reg.hint("broadcast") if broadcast else reg.hint("merge")
        res = enc.join(r.select("facility_id", "facility_type"), "facility_id").groupBy("facility_type").count().collect()
        spark.conf.set("spark.sql.adaptive.enabled", "true")
        return res
    before, _ = timed(lambda: join_count(False)); m1 = stage_summary(spark)
    after, _ = timed(lambda: join_count(True)); m2 = stage_summary(spark)
    results.append(dict(experiment="1 broadcast join of facility registry", rows=n, **delta(before, after),
                        cause="sort-merge join shuffles every encounter by facility_id; a 650-row table fits in memory",
                        metrics_after_both=m2))
    spark.conf.set("spark.sql.autoBroadcastJoinThreshold", "10485760")

    # 2. partition pruning -----------------------------------------------------------------------------
    flat = config.LAKE / "_exp_flat"
    shutil.rmtree(flat, ignore_errors=True)
    spark.read.parquet(str(sil)).drop("epi_year", "epi_week").withColumn("epi_year", F.year("week_start")).write.mode("overwrite").parquet(str(flat))
    target = spark.read.parquet(str(sil)).agg(F.max("epi_year")).first()[0]
    ws = spark.read.parquet(str(sil)).where(F.col("epi_year") == target).agg(F.min("epi_week")).first()[0] + 10
    q_part = lambda: spark.read.parquet(str(sil)).where((F.col("epi_year") == target) & (F.col("epi_week") == ws)).groupBy("disease_group").count().collect()
    q_flat = lambda: spark.read.parquet(str(flat)).where((F.col("epi_year") == target) & (F.col("week_start") >= F.date_add(F.lit(f"{target}-01-01").cast("date"), 7 * (ws - 1))) & (F.col("week_start") < F.date_add(F.lit(f"{target}-01-01").cast("date"), 7 * ws))).groupBy("disease_group").count().collect()
    before, _ = timed(q_flat); after, _ = timed(q_part)
    parts_all = spark.read.parquet(str(sil)).rdd.getNumPartitions()
    parts_pruned = spark.read.parquet(str(sil)).where((F.col("epi_year") == target) & (F.col("epi_week") == ws)).rdd.getNumPartitions()
    plan = spark.read.parquet(str(sil)).where((F.col("epi_year") == target) & (F.col("epi_week") == ws))._jdf.queryExecution().simpleString().split("PartitionFilters")[-1][:120]
    results.append(dict(experiment="2 partition pruning by epi_year/epi_week", rows=n, **delta(before, after),
                        input_partitions_without_pruning=parts_all, input_partitions_with_pruning=parts_pruned,
                        plan_fragment=plan, cause="a one-week query reads one partition directory instead of scanning every file"))
    # same comparison through the catalog (partition metadata in the metastore, no directory listing per query)
    cat_part = cat_flat = None
    try:
        spark.sql("CREATE DATABASE IF NOT EXISTS episcope")
        spark.sql("DROP TABLE IF EXISTS episcope.exp_flat")
        spark.sql(f"CREATE TABLE episcope.exp_flat USING PARQUET LOCATION '{flat.as_uri()}'")
        lo = f"date_add(to_date('{target}-01-01'), {7 * (ws - 1)})"
        hi = f"date_add(to_date('{target}-01-01'), {7 * ws})"
        qc_part = lambda: spark.sql(f"SELECT disease_group, count(*) c FROM episcope.silver_encounters WHERE epi_year={target} AND epi_week={ws} GROUP BY disease_group").collect()
        qc_flat = lambda: spark.sql(f"SELECT disease_group, count(*) c FROM episcope.exp_flat WHERE epi_year={target} AND week_start >= {lo} AND week_start < {hi} GROUP BY disease_group").collect()
        cat_flat, _ = timed(qc_flat); cat_part, _ = timed(qc_part)
        spark.sql("DROP TABLE IF EXISTS episcope.exp_flat")
    except Exception as exc:
        print("catalog comparison skipped:", str(exc)[:120])
    results[-1]["catalog_unpartitioned_s"] = cat_flat
    results[-1]["catalog_partitioned_s"] = cat_part
    shutil.rmtree(flat, ignore_errors=True)

    # 3. skew: Dhaka-heavy aggregation, salting and AQE ------------------------------------------------
    big = enc.union(enc).union(enc).union(enc)           # multiply rows so the stage is measurable
    def agg_plain():
        return big.groupBy("district_id", "disease_group").count().agg(F.sum("count")).collect()
    def agg_salted():
        s = big.withColumn("salt", (F.rand(7) * 8).cast("int"))
        return s.groupBy("district_id", "disease_group", "salt").count().groupBy("district_id", "disease_group").agg(F.sum("count").alias("count")).agg(F.sum("count")).collect()
    spark.conf.set("spark.sql.adaptive.enabled", "false"); spark.conf.set("spark.sql.shuffle.partitions", "8")
    t_plain, _ = timed(agg_plain); t_salt, _ = timed(agg_salted)
    spark.conf.set("spark.sql.adaptive.enabled", "true")
    t_aqe, _ = timed(agg_plain)
    share = enc.groupBy("district_id").count().orderBy(F.desc("count")).limit(3).collect()
    tot = sum(r["count"] for r in enc.groupBy("district_id").count().collect())
    results.append(dict(experiment="3 skewed aggregation (largest district share %.1f%%)" % (100 * share[0]["count"] / tot), rows=n * 4,
                        plain_aqe_off_s=t_plain, salted_aqe_off_s=t_salt, plain_aqe_on_s=t_aqe,
                        top_districts=[(r["district_id"], r["count"]) for r in share],
                        cause="hash partitioning sends all rows of the largest district to one task; salting spreads them, AQE coalesces small partitions"))

    # 4. file compaction -------------------------------------------------------------------------------
    small, compact = config.LAKE / "_exp_small", config.LAKE / "_exp_compact"
    for p in (small, compact):
        shutil.rmtree(p, ignore_errors=True)
    full = spark.read.parquet(str(sil))
    full.repartition(200).write.mode("overwrite").parquet(str(small))
    spark.read.parquet(str(small)).coalesce(4).write.mode("overwrite").parquet(str(compact))
    scan = lambda p: spark.read.parquet(str(p)).where(F.col("disease_group") == "dengue").groupBy("district_id").count().collect()
    t_small, _ = timed(lambda: scan(small)); t_comp, _ = timed(lambda: scan(compact))
    nf = lambda p: len(list(pathlib.Path(p).glob("*.parquet")))
    results.append(dict(experiment="4 file compaction", rows=n, files_before=nf(small), files_after=nf(compact), **delta(t_small, t_comp),
                        cause="hundreds of tiny files cost task scheduling and open/footer overhead; compaction reduces tasks"))
    for p in (small, compact):
        shutil.rmtree(p, ignore_errors=True)
    OUT.mkdir(exist_ok=True)
    (OUT / "spark_optimisations.json").write_text(json.dumps(results, indent=1, default=str))
    print(json.dumps(results, indent=1, default=str))
    print("Spark UI was at", spark.sparkContext.uiWebUrl)
    spark.stop()


if __name__ == "__main__":
    main()
