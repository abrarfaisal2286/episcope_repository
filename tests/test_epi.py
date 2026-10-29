import datetime as dt
import random
from pyspark.sql import functions as F
from common.epi import week_start, epi_year_week, epi_week_start, week_start_col, epi_year_col, epi_week_col


def test_python_matches_spark(spark):
    random.seed(3)
    days = [dt.date(2021, 1, 1) + dt.timedelta(days=random.randint(0, 2000)) for _ in range(300)] + [dt.date(2021, 1, 1), dt.date(2024, 12, 31), dt.date(2022, 12, 31)]
    df = spark.createDataFrame([(d,) for d in days], ["d"]).withColumn("ws", week_start_col(F.col("d")))
    df = df.withColumn("y", epi_year_col(F.col("ws"))).withColumn("w", epi_week_col(F.col("ws")))
    for r in df.collect():
        ws = week_start(r["d"])
        assert ws == r["ws"] and (r["y"], r["w"]) == epi_year_week(ws)


def test_inverse():
    for y in (2024, 2025, 2026):
        for w in (1, 20, 52):
            ws = epi_week_start(y, w)
            assert ws.weekday() == 6 and epi_year_week(ws) == (y, w)
