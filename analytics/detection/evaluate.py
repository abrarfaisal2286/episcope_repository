"""Evaluation of detection against injected ground truth (AN-2, AN-3, conflict C1).

This is the ONLY module that reads ground_truth_*.csv. It reports sensitivity, false alert episodes
per district-year and median days to detection for a sweep of thresholds, selects an operating point
and tests the reporting-artefact guard.
"""
import json
import sys
import numpy as np
import pandas as pd
from common import config
from common.io import read_table

MIN_COUNT = 5
C2_GRID = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0]
FL_GRID = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0]
CUSUM_GRID = [2.0, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0, 30.0]
MAX_FALSE_PER_DISTRICT_YEAR = 1.0
EVAL_START = [pd.Timestamp("1900-01-01")]            # set in main(): first week every method can score


def alert_flags(scores: pd.DataFrame, method: str, thr: float, guard: bool = True) -> pd.Series:
    if method == "c2":
        a = scores["z_c2"] >= thr
    elif method == "fl":
        a = scores["z_fl"] >= thr
    elif method == "cusum":
        a = scores["cusum"] >= thr
    elif method == "c2_naive":
        a = scores["naive_z_c2"] >= thr
    else:
        raise ValueError(method)
    a = a & (scores["cases"] >= MIN_COUNT)
    if guard and method != "c2_naive":
        a = a & ~scores["artefact_flag"]
    return a.fillna(False)


def episodes(scores: pd.DataFrame, flags: pd.Series) -> pd.DataFrame:
    s = scores.loc[flags, ["district_id", "disease_group", "report_week_start"]].copy()
    s["report_week_start"] = pd.to_datetime(s["report_week_start"])
    s = s.sort_values(["district_id", "disease_group", "report_week_start"])
    if s.empty:
        return pd.DataFrame({"district_id": pd.Series(dtype="object"), "disease_group": pd.Series(dtype="object"),
                             "first_week": pd.Series(dtype="datetime64[ns]"), "last_week": pd.Series(dtype="datetime64[ns]")})
    gap = s.groupby(["district_id", "disease_group"])["report_week_start"].diff().dt.days
    s["ep"] = ((gap.isna()) | (gap > 7)).cumsum()
    return (s.groupby("ep").agg(district_id=("district_id", "first"), disease_group=("disease_group", "first"),
                                first_week=("report_week_start", "min"), last_week=("report_week_start", "max"))
            .reset_index(drop=True))


def evaluate_threshold(scores, truth, art, method, thr, guard=True):
    flags = alert_flags(scores, method, thr, guard)
    ep = episodes(scores, flags)
    truth = truth[truth["disease"].isin(config.DISEASES)].copy()
    truth["start"] = pd.to_datetime(truth["start_date"]); truth["end"] = pd.to_datetime(truth["end_date"])
    truth = truth[truth["start"] >= EVAL_START[0]].copy()
    detected, days = [], []
    for _, o in truth.iterrows():
        e = ep[(ep["district_id"] == o["district_id"]) & (ep["disease_group"] == o["disease"])]
        # an alert matches if its week overlaps [start, end + 7 days]
        hit = e[(e["last_week"] + pd.Timedelta(days=6) >= o["start"]) & (e["first_week"] <= o["end"] + pd.Timedelta(days=7))]
        if len(hit):
            # first alert week inside the window
            fl = scores.loc[flags & (scores["district_id"] == o["district_id"])
                            & (scores["disease_group"] == o["disease"])].copy()
            fl["w"] = pd.to_datetime(fl["report_week_start"])
            fl = fl[(fl["w"] + pd.Timedelta(days=6) >= o["start"]) & (fl["w"] <= o["end"] + pd.Timedelta(days=7))]
            detected.append(True)
            days.append(max(0, (fl["w"].min() + pd.Timedelta(days=6) - o["start"]).days))
        else:
            detected.append(False)
    truth["detected"] = detected
    # false alert episodes: no overlap with any true outbreak window (+/- tolerance) in same district-disease
    false_eps = 0
    for _, e in ep.iterrows():
        t = truth[(truth["district_id"] == e["district_id"]) & (truth["disease"] == e["disease_group"])]
        ok = ((e["last_week"] + pd.Timedelta(days=6) >= t["start"] - pd.Timedelta(days=7))
              & (e["first_week"] <= t["end"] + pd.Timedelta(days=14))).any() if len(t) else False
        false_eps += 0 if ok else 1
    weeks = pd.to_datetime(scores["report_week_start"])
    years = max(1e-9, (weeks.max() - EVAL_START[0]).days / 365.25)
    n_dist = scores["district_id"].nunique()
    local = truth["outbreak_type"] != "nationwide_respiratory"
    return dict(method=method, threshold=thr, guard=guard,
                events=int(len(truth)), detected=int(truth["detected"].sum()),
                sensitivity=float(truth["detected"].mean()) if len(truth) else np.nan,
                sensitivity_local=float(truth.loc[local, "detected"].mean()) if local.any() else np.nan,
                false_alert_episodes=int(false_eps),
                false_per_district_year=float(false_eps / (n_dist * years)),
                median_days_to_detection=float(np.median(days)) if days else np.nan)


def artefact_test(scores, art, method, thr):
    """Alerts in the artefact district around the back-fill week with and without the guard."""
    res = {}
    for _, a in art.iterrows():
        bf = pd.Timestamp(a["backfill_date"])
        wk = scores["report_week_start"].astype("datetime64[ns]")
        m = (scores["district_id"] == a["district_id"]) & (wk >= bf - pd.Timedelta(days=14)) & (wk <= bf + pd.Timedelta(days=7))
        res[a["facility_id"]] = dict(
            district_id=a["district_id"], backfill_date=str(a["backfill_date"]),
            naive_alert_weeks=int(alert_flags(scores[m], "c2_naive", thr).sum()),
            guarded_alert_weeks=int(alert_flags(scores[m], method, thr, guard=True).sum()),
            artefact_weeks_flagged=int(scores.loc[m, "artefact_flag"].sum()))
    return res


def main(tag="history"):
    scores = read_table("gold", "detection_scores")
    truth = pd.read_csv(config.GROUND_TRUTH / f"ground_truth_outbreaks_{tag}.csv")
    art = pd.read_csv(config.GROUND_TRUTH / f"ground_truth_artefacts_{tag}.csv")
    scores["report_week_start"] = pd.to_datetime(scores["report_week_start"])
    first_fl = scores.loc[scores["z_fl"].notna(), "report_week_start"].min()
    EVAL_START[0] = first_fl if pd.notna(first_fl) else scores["report_week_start"].min()
    scores = scores[scores["report_week_start"] >= EVAL_START[0]].reset_index(drop=True)   # common evaluation period
    rows = []
    for thr in C2_GRID:
        rows.append(evaluate_threshold(scores, truth, art, "c2", thr))
    for thr in FL_GRID:
        rows.append(evaluate_threshold(scores, truth, art, "fl", thr))
    for thr in CUSUM_GRID:
        rows.append(evaluate_threshold(scores, truth, art, "cusum", thr))
    for thr in C2_GRID:
        rows.append(evaluate_threshold(scores, truth, art, "c2_naive", thr, guard=False))
    curve = pd.DataFrame(rows)
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    curve.to_csv(config.OUTPUTS / f"detection_tradeoff_{tag}.csv", index=False)
    cand = curve[(curve["method"] != "c2_naive") & (curve["false_per_district_year"] <= MAX_FALSE_PER_DISTRICT_YEAR)]
    if cand.empty:
        cand = curve[curve["method"] != "c2_naive"].nsmallest(1, "false_per_district_year")
    best = cand.sort_values(["sensitivity", "median_days_to_detection"], ascending=[False, True]).iloc[0]
    per_method = {}
    for m in ("c2", "fl", "cusum"):
        c = curve[(curve["method"] == m) & (curve["false_per_district_year"] <= MAX_FALSE_PER_DISTRICT_YEAR)]
        if c.empty:
            c = curve[curve["method"] == m].nsmallest(1, "false_per_district_year")
        per_method[m] = c.sort_values(["sensitivity", "median_days_to_detection"], ascending=[False, True]).iloc[0].to_dict()
    op = dict(method=str(best["method"]), threshold=float(best["threshold"]), min_count=MIN_COUNT, evaluation_start=str(EVAL_START[0].date()),
              rule=f"max sensitivity subject to <= {MAX_FALSE_PER_DISTRICT_YEAR} false alert episodes per district-year",
              metrics={k: (None if (isinstance(v, float) and np.isnan(v)) else v) for k, v in best.to_dict().items()},
              per_method={m: {k: (None if (isinstance(v, float) and np.isnan(v)) else v) for k, v in d.items()}
                          for m, d in per_method.items()})
    op["artefact_test"] = artefact_test(scores, art, op["method"], op["threshold"])
    (config.OUTPUTS / f"operating_point_{tag}.json").write_text(json.dumps(op, indent=1, default=str))
    if tag == "history":
        (config.OUTPUTS / "operating_point.json").write_text(json.dumps(op, indent=1, default=str))
    print(json.dumps(op, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "history")
