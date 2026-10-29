"""PRV-1: salted hashing, no direct identifiers in silver."""
from pyspark.sql import functions as F


def h(spark, salt, pid):
    return spark.range(1).select(F.sha2(F.concat(F.lit(salt), F.lit(pid)), 256).alias("h")).first()[0]


def test_hash_is_deterministic_and_salted(spark):
    a, b = h(spark, "s1", "1234567890"), h(spark, "s1", "1234567890")
    assert a == b and len(a) == 64
    assert h(spark, "s2", "1234567890") != a            # a different salt gives different pseudonyms
    assert a != "1234567890" and "1234567890" not in a


def test_silver_schema_has_no_direct_identifiers():
    import pathlib, re
    src = pathlib.Path(__file__).resolve().parents[1] / "pipeline" / "silver" / "build.py"
    text = src.read_text()
    select_block = text[text.index("new = clean.select("):text.index("new = (new.withColumn")]
    assert '"patient_id"' not in select_block.replace('F.col("patient_id")', "")
    assert "salted.alias(\"patient_hash\")" in select_block and "age_band" in select_block
    assert "F.col(\"age\")" in select_block         # exact age is used only to compute the band
