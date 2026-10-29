"""DQ-3 lineage: trace a gold record back to its bronze batches and source files.

Usage: python scripts/trace.py <district_id> <disease> <week_start YYYY-MM-DD>
Example: python scripts/trace.py BD3026 dengue 2025-09-07
"""
import sys
import pandas as pd
from common import config
from common.io import read_table
from common.spark_utils import read_ledger


def main(did, disease, week):
    w = read_table("gold", "weekly_cases")
    w["week_start"] = pd.to_datetime(w["week_start"])
    r = w[(w["district_id"] == did) & (w["disease_group"] == disease) & (w["week_start"] == pd.Timestamp(week))]
    if r.empty:
        raise SystemExit("no such gold record")
    r = r.iloc[0]
    batches = list(r["source_batch_ids"])
    led = {v["batch_id"]: (k, v) for k, v in read_ledger("bronze_encounters").items()}
    print(f"gold record: district={did} disease={disease} week_start={week} cases={r['cases']} built_at={r['built_at']}")
    print(f"{len(batches)} bronze batch(es):")
    for b in batches:
        if b in led:
            sha, v = led[b]
            print(f"  {b}  file={v['file']}  source_date={v['source_date']}  sha256={sha[:16]}...  loaded_at={v['loaded_at']}")
        else:
            print(f"  {b}  (not in ledger)")


if __name__ == "__main__":
    main(*sys.argv[1:4])
