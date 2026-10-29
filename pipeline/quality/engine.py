"""Rule engine: evaluate violation expressions, split valid and quarantined rows (DQ-1)."""
from pyspark.sql import functions as F
from pyspark import StorageLevel


def evaluate(df, rules):
    """rules: list of (code, boolean Column that is TRUE when the row violates the rule).

    Returns the dataframe with an array column `_reasons` (persisted, because it is reused)."""
    reasons = F.array_compact(F.array(*[F.when(F.coalesce(expr, F.lit(False)), F.lit(code))
                                        for code, expr in rules]))
    return df.withColumn("_reasons", reasons).persist(StorageLevel.MEMORY_AND_DISK)


def split(d):
    valid = d.where(F.size("_reasons") == 0).drop("_reasons")
    invalid = (d.where(F.size("_reasons") > 0)
               .withColumn("_reason_codes", F.concat_ws("|", "_reasons")).drop("_reasons"))
    return valid, invalid


def failure_counts(d):
    return (d.select("_batch_id", F.explode("_reasons").alias("rule_code"))
            .groupBy("_batch_id", "rule_code").count().withColumnRenamed("count", "rule_fail_count"))


def dq_rows(spark, d, source, rule_codes, extra_fail=None):
    """Per-batch quality report rows: one row per batch x rule (pass rates, quarantined counts)."""
    base = d.groupBy("_batch_id").agg(
        F.first("_source_date").alias("source_date"), F.count("*").alias("rows_in"),
        F.sum(F.when(F.size("_reasons") > 0, 1).otherwise(0)).alias("rows_failed_validity"))
    rules_df = spark.createDataFrame([(c,) for c in rule_codes], ["rule_code"])
    fails = failure_counts(d)
    if extra_fail is not None:                       # duplicate counts computed after validity
        fails = fails.unionByName(extra_fail)
    grid = base.crossJoin(rules_df).join(fails, ["_batch_id", "rule_code"], "left")
    return (grid.withColumn("rule_fail_count", F.coalesce("rule_fail_count", F.lit(0)))
            .withColumn("rule_pass_rate", F.round(1 - F.col("rule_fail_count") / F.col("rows_in"), 6))
            .withColumn("source", F.lit(source))
            .withColumnRenamed("_batch_id", "batch_id"))
