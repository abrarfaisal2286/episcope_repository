"""ING-4: idempotent incremental loads and late-record updates (integration test, runs Spark; ~5 min)."""
import os
import subprocess
import sys
import pathlib
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.slow


def sh(mod, env, *args):
    r = subprocess.run([sys.executable, "-m", mod, *args], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


def load(env):
    for m in ("pipeline.bronze.ingest", "pipeline.silver.build", "pipeline.gold.build"):
        sh(m, env)


def counts(root):
    s = pd.read_parquet(root / "lake" / "silver" / "encounters")
    w = pd.read_parquet(root / "lake" / "gold" / "weekly_cases")
    q = pd.read_parquet(root / "lake" / "silver" / "quarantine")
    return len(s), int(w["cases"].sum()), len(q), s, w


def test_idempotent_and_late_updates(tmp_path):
    env = dict(os.environ, EPISCOPE_DATA=str(tmp_path), EPISCOPE_SALT="unit-test-salt", EPISCOPE_CATALOG="memory", SPARK_UI="false")
    sh("generator.generate", env, "--sf", "0.004", "--seed", "5", "--start", "2021-01-01", "--end", "2021-02-28")
    load(env)
    n1, cases1, q1, silver, weekly = counts(tmp_path)
    assert n1 > 1000 and q1 > 0
    load(env)                                            # re-run the same drop: nothing may change
    n2, cases2, q2, _, _ = counts(tmp_path)
    assert (n2, cases2, q2) == (n1, cases1, q1)
    # --- a late record: onset 18 days before its report date, plus one resubmitted encounter
    raw = tmp_path / "raw" / "encounters"
    last = sorted(raw.glob("encounters_*.csv"))[-1]
    sample = pd.read_csv(last, dtype=str)
    base = sample[sample["icd10_code"].str.startswith("A90")].head(1)
    if base.empty:
        base = sample.head(1)
    late = pd.concat([base.assign(encounter_id=f"LATE{i}", patient_id=f"99900000{i}", onset_date="2021-01-20",
                                  report_date="2021-03-10", sex="M", age_years="30", outcome="recovered") for i in range(3)])
    late["icd10_code"] = "A90"; late["lab_confirmed"] = "1"; late["admitted"] = "0"
    resub = silver.head(1)[["encounter_id"]].merge(sample.head(1).drop(columns=["encounter_id"]), how="cross")
    resub["encounter_id"] = silver.iloc[0]["encounter_id"]
    resub["onset_date"] = str(silver.iloc[0]["onset_date"]); resub["report_date"] = "2021-03-10"
    resub["facility_id"] = silver.iloc[0]["facility_id"]; resub["icd10_code"] = silver.iloc[0]["icd10_code"]
    pd.concat([late, resub[late.columns]]).to_csv(raw / "encounters_20210310.csv", index=False)
    load(env)
    n3, cases3, q3, _, weekly3 = counts(tmp_path)
    assert n3 == n1 + 3                                   # three late records added, the resubmission rejected
    assert cases3 == cases1 + 3                           # gold reflects the late records
    assert q3 == q1 + 1                                   # duplicate went to quarantine
    wk = pd.Timestamp("2021-01-17")                       # Sunday week containing 2021-01-20
    before = weekly[(pd.to_datetime(weekly["week_start"]) == wk) & (weekly["disease_group"] == "dengue")]["cases"].sum()
    after = weekly3[(pd.to_datetime(weekly3["week_start"]) == wk) & (weekly3["disease_group"] == "dengue")]["cases"].sum()
    assert after == before + 3                            # only the affected epi-week changed
    load(env)                                             # and again: still idempotent
    assert counts(tmp_path)[:3] == (n3, cases3, q3)
