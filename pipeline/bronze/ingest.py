"""Bronze layer: immutable raw copies with ingestion metadata (ING-1, ING-4).

Every landed file is fingerprinted (sha256). The ledger records which checksums were loaded, so
re-running the same drop is a no-op (idempotent). Bronze keeps all columns as strings, unchanged.
Metadata added: _source, _batch_id, _file_checksum, _load_ts, _source_file, _source_date, source_month.
"""
import datetime as dt
import re
import sys
from pyspark.sql import functions as F
from common import config
from common.spark_utils import get_spark, read_ledger, write_ledger, sha256_file

SOURCES = {
    # name: (landing glob dir, pattern, reader, partition by month?)
    "encounters": ("encounters", "*.csv", "csv", True),
    "weather": ("weather", "weather_*.csv", "csv", True),
    "stock": ("stock", "*.jsonl", "json", True),
    "registry": ("reference", "facility_registry.csv", "csv", False),
    "districts": ("reference", "districts.csv", "csv", False),
}
_DATE_RE = re.compile(r"(\d{8})")


def _source_date(name: str, fallback: str) -> str:
    m = _DATE_RE.findall(name)
    if m:
        d = m[0]
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return fallback


def ingest_source(spark, source: str) -> dict:
    sub, pattern, reader, partitioned = SOURCES[source]
    files = sorted((config.RAW / sub).glob(pattern))
    ledger = read_ledger(f"bronze_{source}")
    today = dt.date.today().isoformat()
    new = []
    for f in files:
        checksum = sha256_file(f)
        if checksum in ledger:
            continue
        new.append(dict(path=str(f), name=f.name, checksum=checksum, batch_id="B" + checksum[:12],
                        source_date=_source_date(f.name, today)))
    if not new:
        return dict(source=source, new_files=0, rows=0)
    meta = spark.createDataFrame(
        [(("file://" + n["path"]), n["batch_id"], n["checksum"], n["source_date"], n["name"]) for n in new],
        ["_path", "_batch_id", "_file_checksum", "_source_date", "_source_file"])
    paths = [n["path"] for n in new]
    if reader == "csv":
        df = (spark.read.option("header", True).option("inferSchema", False)
              .option("mode", "PERMISSIVE").csv(paths))
    else:
        df = spark.read.option("mode", "PERMISSIVE").json(paths)
        df = df.select([F.col(c).cast("string").alias(c) for c in df.columns])
    df = (df.withColumn("_path", F.input_file_name())
          .join(F.broadcast(meta), "_path", "left").drop("_path")
          .withColumn("_source", F.lit(source))
          .withColumn("_load_ts", F.lit(dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")))
          .withColumn("source_month", F.substring("_source_date", 1, 7)))
    out = config.BRONZE / source
    writer = df.write.mode("append").option("compression", "snappy")
    if partitioned:
        writer = writer.partitionBy("source_month")
    else:
        # reference data: keep every version; silver takes the latest batch
        writer = writer.partitionBy("source_month")
    writer.parquet(str(out))
    rows = spark.read.parquet(str(out)).where(F.col("_batch_id").isin([n["batch_id"] for n in new])).count()
    for n in new:                                    # commit to ledger only after the write succeeded
        ledger[n["checksum"]] = dict(batch_id=n["batch_id"], file=n["name"], source_date=n["source_date"],
                                     loaded_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"))
    write_ledger(f"bronze_{source}", ledger)
    return dict(source=source, new_files=len(new), rows=rows)


def main(sources=None):
    spark = get_spark("bronze")
    res = [ingest_source(spark, s) for s in (sources or SOURCES)]
    for r in res:
        print(r)
    spark.stop()
    return res


if __name__ == "__main__":
    main(sys.argv[1:] or None)
