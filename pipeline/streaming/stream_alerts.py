"""Near-real-time path (ING-6): Spark Structured Streaming over a file source.

New encounter files dropped into data/raw/encounters_stream/ are validated with the same rules as the batch
silver stage, de-identified, and folded into live per-week counts. A PROVISIONAL alert is raised as soon as the
count already received for the current report week exceeds the EARS C2 threshold of a complete week (baseline
mean + threshold x sd from the latest batch scores). Because partial counts only grow, such an alert would also
fire at the end of the week: it is an early, conservative signal and needs no guessing of the missing days.

Latency is measured per row from the file modification time to the time the alert table was written.
Run:   python -m pipeline.streaming.stream_alerts            (continuous, 30 s micro-batches)
       python -m pipeline.streaming.stream_alerts --once      (process what is there, then stop)
"""
import json
import shutil
import sys
import time
from pyspark.sql import functions as F, types as T
from common import config
from common.epi import week_start_col
from common.io import read_table
from common.spark_utils import get_spark
from pipeline.quality.rules import encounter_rules

STREAM_IN = config.RAW / "encounters_stream"
CHECKPOINT = config.LAKE / "_checkpoints" / "alerts_live"
OUT = config.GOLD / "alerts_live"
COLS = ["encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "age_years", "sex",
        "icd10_code", "lab_confirmed", "admitted", "outcome"]


def thresholds():
    """Latest complete-week baseline per district and disease from the batch detection scores."""
    sc = read_table("gold", "detection_scores")
    sc = sc[sc["baseline_mean"].notna()].sort_values("report_week_start").groupby(["district_id", "disease_group"]).tail(1)
    op = json.loads((config.OUTPUTS / "operating_point.json").read_text()) if (config.OUTPUTS / "operating_point.json").exists() else dict(threshold=3.0, min_count=5)
    sc["threshold_cases"] = sc["baseline_mean"] + op["threshold"] * sc["baseline_sd"]
    return sc[["district_id", "disease_group", "threshold_cases"]], op["min_count"]


def run(spark, once=False, interval="30 seconds"):
    STREAM_IN.mkdir(parents=True, exist_ok=True)
    schema = T.StructType([T.StructField(c, T.StringType()) for c in COLS])
    reg = spark.read.parquet(str(config.SILVER / "registry")).select("facility_id", F.col("district_id").alias("reg_district_id"))
    dist = spark.read.parquet(str(config.SILVER / "districts")).select(F.col("district_id").alias("geo_district_id"))
    thr_pdf, min_count = thresholds()
    thr = spark.createDataFrame(thr_pdf)
    src = (spark.readStream.schema(schema).option("header", True).option("maxFilesPerTrigger", 20).csv(str(STREAM_IN))
           .withColumn("_file_mtime", F.col("_metadata.file_modification_time")))
    p = (src.withColumn("onset", F.to_date("onset_date", "yyyy-MM-dd")).withColumn("report", F.to_date("report_date", "yyyy-MM-dd"))
         .withColumn("age", F.col("age_years").cast("int")).withColumn("lab", F.col("lab_confirmed").cast("int"))
         .withColumn("adm", F.col("admitted").cast("int"))
         .join(F.broadcast(reg), "facility_id", "left").join(F.broadcast(dist), F.col("reg_district_id") == F.col("geo_district_id"), "left"))
    bad = F.array_compact(F.array(*[F.when(F.coalesce(expr, F.lit(False)), F.lit(code)) for code, expr in encounter_rules()]))
    valid = p.where(F.size(bad) == 0)
    live = (valid.withColumn("disease_group", F.create_map(*[x for kv in config.ICD_TO_DISEASE.items() for x in (F.lit(kv[0]), F.lit(kv[1]))])[F.substring("icd10_code", 1, 3)])
            .where(F.col("disease_group").isNotNull())
            .select("encounter_id", F.col("reg_district_id").alias("district_id"), "disease_group",
                    week_start_col(F.col("report")).alias("report_week_start"), "_file_mtime"))

    def process(batch, batch_id):
        if batch.rdd.isEmpty():
            return
        inc = (batch.dropDuplicates(["encounter_id"]).groupBy("district_id", "disease_group", "report_week_start")
               .agg(F.count("*").alias("new_cases"), F.max("_file_mtime").alias("latest_file_mtime")))
        prev = spark.read.parquet(str(OUT)).select("district_id", "disease_group", "report_week_start", F.col("cases_so_far").alias("prev")) \
            if OUT.exists() else spark.createDataFrame([], "district_id string, disease_group string, report_week_start date, prev long")
        merged = (inc.join(prev, ["district_id", "disease_group", "report_week_start"], "left")
                  .withColumn("cases_so_far", F.col("new_cases") + F.coalesce("prev", F.lit(0)))
                  .join(thr, ["district_id", "disease_group"], "left")
                  .withColumn("provisional_alert", (F.col("cases_so_far") >= F.col("threshold_cases")) & (F.col("cases_so_far") >= min_count))
                  .withColumn("processed_at", F.current_timestamp())
                  .withColumn("latency_seconds", F.unix_timestamp("processed_at") - F.unix_timestamp("latest_file_mtime"))
                  .select("district_id", "disease_group", "report_week_start", "cases_so_far", "threshold_cases",
                          "provisional_alert", "latest_file_mtime", "processed_at", "latency_seconds"))
        keep = (spark.read.parquet(str(OUT)).join(inc.select("district_id", "disease_group", "report_week_start"),
                                                  ["district_id", "disease_group", "report_week_start"], "left_anti")
                if OUT.exists() else None)
        tmp = config.LAKE / "_stage" / "alerts_live"
        shutil.rmtree(tmp, ignore_errors=True)
        (merged if keep is None else keep.unionByName(merged)).coalesce(1).write.mode("overwrite").parquet(str(tmp))
        spark.read.parquet(str(tmp)).coalesce(1).write.mode("overwrite").parquet(str(OUT))
        shutil.rmtree(tmp, ignore_errors=True)

    w = live.writeStream.foreachBatch(process).option("checkpointLocation", str(CHECKPOINT))
    q = (w.trigger(availableNow=True) if once else w.trigger(processingTime=interval)).start()
    q.awaitTermination()


if __name__ == "__main__":
    spark = get_spark("stream_alerts")
    run(spark, once="--once" in sys.argv)
    spark.stop()
