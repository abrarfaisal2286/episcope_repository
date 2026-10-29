"""Print row counts of the key tables (used to prove idempotency)."""
import json
import pandas as pd
from common.io import read_table
s = read_table("silver", "encounters", columns=["encounter_id"])
w = read_table("gold", "weekly_cases", columns=["cases"])
q = read_table("silver", "quarantine", columns=["_reason_codes"])
print(json.dumps(dict(silver_rows=len(s), silver_unique_ids=int(s["encounter_id"].nunique()), gold_cases=int(w["cases"].sum()), quarantine_rows=len(q)), indent=1))
