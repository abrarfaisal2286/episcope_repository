"""Spark session factory, ledger helpers and catalog registration."""
import hashlib
import json
import os
import pathlib
from common import config

_CATALOG_TABLES = {
    "bronze_encounters": ("bronze/encounters", True), "bronze_weather": ("bronze/weather", True),
    "bronze_stock": ("bronze/stock", True), "bronze_registry": ("bronze/registry", False),
    "bronze_districts": ("bronze/districts", False),
    "silver_encounters": ("silver/encounters", True), "silver_quarantine": ("silver/quarantine", True),
    "silver_weather": ("silver/weather", True), "silver_stock": ("silver/stock", True),
    "silver_registry": ("silver/registry", False), "silver_districts": ("silver/districts", False),
    "gold_cases_triangle": ("gold/cases_triangle", True), "gold_weekly_cases": ("gold/weekly_cases", True),
    "gold_weekly_reports": ("gold/weekly_reports", True),
    "gold_report_completeness": ("gold/report_completeness", True),
    "gold_weather_weekly": ("gold/weather_weekly", True),
    "gold_stock_district_week": ("gold/stock_district_week", False),
    "gold_dq_report": ("gold/dq_report", False),
}


def get_spark(app: str = "episcope"):
    from pyspark.sql import SparkSession
    config.ensure_dirs()
    b = (SparkSession.builder.master(config.SPARK_MASTER).appName(app)
         .config("spark.driver.memory", config.SPARK_DRIVER_MEMORY)
         .config("spark.sql.shuffle.partitions", config.SHUFFLE_PARTITIONS)
         .config("spark.sql.adaptive.enabled", "true")
         .config("spark.sql.adaptive.skewJoin.enabled", "true")
         .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
         .config("spark.sql.legacy.timeParserPolicy", "CORRECTED")
         .config("spark.sql.session.timeZone", "UTC")
         .config("spark.sql.parquet.compression.codec", "snappy")
         .config("spark.sql.warehouse.dir", str(config.LAKE / "warehouse"))
         .config("spark.driver.extraJavaOptions",
                 "-Dio.netty.tryReflectionSetAccessible=true -Djdk.reflect.useDirectMethodHandle=true "
                 "--add-opens=java.base/java.nio=ALL-UNNAMED --add-opens=java.base/sun.nio.ch=ALL-UNNAMED")
         .config("spark.sql.execution.arrow.pyspark.enabled", "true")
         .config("spark.ui.showConsoleProgress", "false")
         .config("spark.ui.enabled", os.environ.get("SPARK_UI", "true")))
    if os.environ.get("EPISCOPE_CATALOG", "hive") == "hive":
        b = (b.config("spark.sql.catalogImplementation", "hive")
             .config("javax.jdo.option.ConnectionURL",
                     f"jdbc:derby:;databaseName={config.LAKE / 'metastore_db'};create=true"))
    spark = b.getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def register_tables(spark) -> list:
    """Register every existing lake table as an external Parquet table in database `episcope`."""
    spark.sql("CREATE DATABASE IF NOT EXISTS episcope")
    done = []
    for name, (rel, partitioned) in _CATALOG_TABLES.items():
        path = config.LAKE / rel
        if not path.exists():
            continue
        spark.sql(f"DROP TABLE IF EXISTS episcope.{name}")
        spark.sql(f"CREATE TABLE episcope.{name} USING PARQUET LOCATION '{path.as_uri()}'")
        if partitioned:
            spark.sql(f"MSCK REPAIR TABLE episcope.{name}")
        done.append(name)
    return done


# ------------------------------------------------------------------ ledger (idempotency)
def sha256_file(path, block=1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def ledger_path(name: str) -> pathlib.Path:
    return config.LEDGER / f"{name}.json"


def read_ledger(name: str) -> dict:
    p = ledger_path(name)
    return json.loads(p.read_text()) if p.exists() else {}


def write_ledger(name: str, data: dict) -> None:
    config.LEDGER.mkdir(parents=True, exist_ok=True)
    tmp = ledger_path(name).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(ledger_path(name))
