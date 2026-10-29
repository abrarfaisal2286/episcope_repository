"""DQ-1: every rule fires on a crafted bad row and does not fire on a good row."""
import datetime as dt
from pyspark.sql import functions as F
from pipeline.quality import engine
from pipeline.quality.rules import encounter_rules

D = dt.date


def make(spark, **over):
    base = dict(encounter_id="E1", patient_id="P1", facility_id="F1", onset=D(2024, 5, 1), report=D(2024, 5, 3),
                age=30, sex="M", icd10_code="A90", lab=1, adm=0, outcome="recovered",
                reg_district_id="BD1", geo_district_id="BD1", _batch_id="B1", _source_date="2024-05-03")
    base.update(over)
    return base


from pyspark.sql import types as T

SCHEMA = T.StructType([
    T.StructField("encounter_id", T.StringType()), T.StructField("patient_id", T.StringType()),
    T.StructField("facility_id", T.StringType()), T.StructField("onset", T.DateType()),
    T.StructField("report", T.DateType()), T.StructField("age", T.IntegerType()),
    T.StructField("sex", T.StringType()), T.StructField("icd10_code", T.StringType()),
    T.StructField("lab", T.IntegerType()), T.StructField("adm", T.IntegerType()),
    T.StructField("outcome", T.StringType()), T.StructField("reg_district_id", T.StringType()),
    T.StructField("geo_district_id", T.StringType()), T.StructField("_batch_id", T.StringType()),
    T.StructField("_source_date", T.StringType())])


def codes(spark, row):
    df = spark.createDataFrame([row], SCHEMA)
    d = engine.evaluate(df, encounter_rules())
    return d.select("_reasons").first()[0]


def test_good_row_passes(spark):
    assert codes(spark, make(spark)) == []


def test_each_rule_fires(spark):
    cases = {
        "R01_NULL_ENCOUNTER_ID": dict(encounter_id=" "), "R02_NULL_PATIENT_ID": dict(patient_id=None),
        "R03_NULL_FACILITY": dict(facility_id=""), "R04_BAD_ONSET_DATE": dict(onset=None),
        "R05_BAD_REPORT_DATE": dict(report=None), "R06_ONSET_AFTER_REPORT": dict(onset=D(2024, 5, 9)),
        "R07_DELAY_TOO_LONG": dict(onset=D(2024, 1, 1)), "R08_IMPLAUSIBLE_DATE": dict(onset=D(2001, 1, 1), report=D(2001, 1, 2)),
        "R09_BAD_AGE": dict(age=212), "R10_BAD_SEX": dict(sex="X"), "R11_BAD_ICD10": dict(icd10_code="Z99X"),
        "R12_BAD_FLAG": dict(lab=2), "R13_BAD_OUTCOME": dict(outcome="unknown"),
        "R14_ORPHAN_FACILITY": dict(reg_district_id=None, geo_district_id=None),
        "R15_INVALID_GEOGRAPHY": dict(geo_district_id=None),
    }
    for code, over in cases.items():
        got = codes(spark, make(spark, **over))
        assert code in got, f"{code} did not fire: {got}"
    assert len(cases) >= 12


def test_valid_and_quarantine_split(spark):
    rows = [make(spark), make(spark, encounter_id="E2", age=-4)]
    d = engine.evaluate(spark.createDataFrame(rows, SCHEMA), encounter_rules())
    valid, invalid = engine.split(d)
    assert valid.count() == 1 and invalid.count() == 1
    assert invalid.first()["_reason_codes"] == "R09_BAD_AGE"
