"""Quality rules for encounter records (DQ-1). Each rule is (code, Column that is TRUE on violation)."""
from pyspark.sql import functions as F
from common import config

RULE_DESCRIPTIONS = {
    "R01_NULL_ENCOUNTER_ID": "encounter_id is missing",
    "R02_NULL_PATIENT_ID": "patient_id is missing",
    "R03_NULL_FACILITY": "facility_id is missing",
    "R04_BAD_ONSET_DATE": "onset_date is not a valid calendar date",
    "R05_BAD_REPORT_DATE": "report_date is not a valid calendar date",
    "R06_ONSET_AFTER_REPORT": "onset_date is later than report_date",
    "R07_DELAY_TOO_LONG": f"report delay exceeds {config.MAX_REPORT_DELAY_DAYS} days",
    "R08_IMPLAUSIBLE_DATE": "date outside 2015-01-01 .. 2100-01-01",
    "R09_BAD_AGE": "age is missing or outside 0..120",
    "R10_BAD_SEX": "sex not in (M, F)",
    "R11_BAD_ICD10": "icd10_code does not match the ICD-10 pattern letter + two digits",
    "R12_BAD_FLAG": "lab_confirmed or admitted not in (0, 1)",
    "R13_BAD_OUTCOME": "outcome not in the allowed list",
    "R14_ORPHAN_FACILITY": "facility_id not found in the facility registry (referential integrity)",
    "R15_INVALID_GEOGRAPHY": "facility district not found in district boundaries",
    "R16_DUPLICATE_IN_BATCH": "same encounter_id submitted more than once in the loaded batches",
    "R17_DUPLICATE_EXISTING": "encounter_id already present in silver",
}


def _blank(c):
    return F.col(c).isNull() | (F.trim(F.col(c)) == "")


def encounter_rules():
    """Expects parsed columns: onset, report (dates), age, lab, adm (ints), reg_district_id, geo_district_id."""
    return [
        ("R01_NULL_ENCOUNTER_ID", _blank("encounter_id")),
        ("R02_NULL_PATIENT_ID", _blank("patient_id")),
        ("R03_NULL_FACILITY", _blank("facility_id")),
        ("R04_BAD_ONSET_DATE", F.col("onset").isNull()),
        ("R05_BAD_REPORT_DATE", F.col("report").isNull()),
        ("R06_ONSET_AFTER_REPORT", F.col("onset") > F.col("report")),
        ("R07_DELAY_TOO_LONG", F.datediff("report", "onset") > config.MAX_REPORT_DELAY_DAYS),
        ("R08_IMPLAUSIBLE_DATE", (F.col("onset") < F.lit("2015-01-01").cast("date")) |
                                  (F.col("report") > F.lit("2100-01-01").cast("date"))),
        ("R09_BAD_AGE", F.col("age").isNull() | (F.col("age") < 0) | (F.col("age") > 120)),
        ("R10_BAD_SEX", ~F.col("sex").isin("M", "F")),
        ("R11_BAD_ICD10", ~F.col("icd10_code").rlike(r"^[A-Z][0-9]{2}$")),
        ("R12_BAD_FLAG", ~F.col("lab").isin(0, 1) | ~F.col("adm").isin(0, 1)),
        ("R13_BAD_OUTCOME", ~F.col("outcome").isin("recovered", "died", "referred", "under_treatment")),
        ("R14_ORPHAN_FACILITY", ~_blank("facility_id") & F.col("reg_district_id").isNull()),
        ("R15_INVALID_GEOGRAPHY", F.col("reg_district_id").isNotNull() & F.col("geo_district_id").isNull()),
    ]
