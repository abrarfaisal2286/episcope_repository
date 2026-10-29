"""Data-quality evaluation against injected defects and duplicates (DQ-1, DQ-4).

Reads ground_truth_duplicates_*.csv (evaluation only) and reports the precision and recall of the
cross-facility patient de-duplication, plus per-rule fail rates from the gold dq_report table.
"""
import json
import pandas as pd
from common import config
from common.io import read_table


def main():
    out = {}
    s = read_table("silver", "encounters", columns=["encounter_id", "is_duplicate_patient"])
    flagged = set(s.loc[s["is_duplicate_patient"], "encounter_id"])
    gts = sorted(config.GROUND_TRUTH.glob("ground_truth_duplicates_*.csv"))
    if gts:
        truth = set(pd.concat([pd.read_csv(g) for g in gts])["duplicate_encounter_id"])
        present = truth & set(s["encounter_id"])               # injected duplicates that passed validation
        tp = len(flagged & truth)
        out["patient_dedup"] = dict(flagged=len(flagged), injected_in_silver=len(present), true_positive=tp,
                                    precision=round(tp / max(1, len(flagged)), 4),
                                    recall=round(tp / max(1, len(present)), 4))
    dq = read_table("gold", "dq_report")
    enc = dq[dq["source"] == "encounters"].groupby("rule_code").agg(fail=("rule_fail_count", "sum")).reset_index()
    tot = int(dq[dq["source"] == "encounters"].drop_duplicates("batch_id")["rows_in"].sum())
    enc["fail_rate"] = (enc["fail"] / max(tot, 1)).round(5)
    out["rows_in"] = tot
    out["rules"] = enc.sort_values("rule_code").to_dict(orient="records")
    q = read_table("silver", "quarantine", columns=["_reason_codes"])
    out["quarantined_rows"] = int(len(q))
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    (config.OUTPUTS / "dq_evaluation.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "rules"}, indent=1))


if __name__ == "__main__":
    main()
