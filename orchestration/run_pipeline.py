"""Orchestrated DAG with retries (ENG-1, ENG-4). Each stage is a module run in a fresh process.

  python -m orchestration.run_pipeline --sf 0.05 --seed 42        # full rebuild: generate -> ... -> serving
  python -m orchestration.run_pipeline --incremental              # new files already landed in data/raw
  python -m orchestration.run_pipeline --only forecast            # one stage

Stage timings are appended to data/logs/stage_times.csv (used for the scalability experiment).
"""
import argparse
import csv
import datetime as dt
import os
import subprocess
import sys
import time
from common import config

PY = sys.executable
STAGES = [
    ("generate", lambda a: [PY, "-m", "generator.generate", "--sf", str(a.sf), "--seed", str(a.seed)]),
    ("bronze", lambda a: [PY, "-m", "pipeline.bronze.ingest"]),
    ("silver", lambda a: [PY, "-m", "pipeline.silver.build"]),
    ("gold", lambda a: [PY, "-m", "pipeline.gold.build"] + (["--full"] if a.full_gold else [])),
    ("detect", lambda a: [PY, "-m", "analytics.detection.detect"]),
    ("evaluate_detection", lambda a: [PY, "-m", "analytics.detection.evaluate", a.truth_tag]),
    ("forecast", lambda a: [PY, "-m", "analytics.forecast.forecast"]),
    ("evaluate_forecast", lambda a: [PY, "-m", "analytics.forecast.evaluate"]),
    ("demand", lambda a: [PY, "-m", "analytics.optimise.demand"]),
    ("allocate", lambda a: [PY, "-m", "analytics.optimise.allocate"]),
    ("calendar", lambda a: [PY, "-m", "analytics.calendar.seasonal_calendar"]),
    ("data_quality_eval", lambda a: [PY, "-m", "pipeline.quality.evaluate_dq"]),
    ("serving", lambda a: [PY, "-m", "serving.export"]),
]


def run_stage(name, cmd, retries, log):
    t0 = time.time()
    status = "failed"
    for attempt in range(1, retries + 2):
        print(f"[{name}] attempt {attempt}: {' '.join(cmd)}", flush=True)
        p = subprocess.run(cmd, cwd=config.ROOT, env=os.environ.copy())
        if p.returncode == 0:
            status = "ok"
            break
        time.sleep(5 * attempt)                                 # back-off before retry
    dur = round(time.time() - t0, 1)
    new = not log.exists()
    with open(log, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "stage", "seconds", "status", "attempts", "sf"])
        w.writerow([dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), name, dur, status,
                    attempt, os.environ.get("EPISCOPE_SF", "")])
    return status == "ok", dur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sf", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--incremental", action="store_true", help="skip generation; process landed files")
    ap.add_argument("--only", default=None)
    ap.add_argument("--stages", default=None, help="comma separated list of stages to run")
    ap.add_argument("--from-stage", default=None)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--full-gold", action="store_true")
    ap.add_argument("--force", action="store_true", help="run analytics even if no new data arrived")
    ap.add_argument("--truth-tag", default="history")
    a = ap.parse_args()
    config.ensure_dirs()
    os.environ["EPISCOPE_SF"] = str(a.sf)
    names = [s[0] for s in STAGES]
    todo = names
    if a.stages:
        todo = a.stages.split(",")
    elif a.only:
        todo = [a.only]
    else:
        if a.incremental:
            todo = [n for n in todo if n != "generate"]
        if a.from_stage:
            todo = todo[todo.index(a.from_stage):]
    log = config.LOGS / "stage_times.csv"
    total = 0.0
    for name, mk in STAGES:
        if name not in todo:
            continue
        if name == "evaluate_detection":
            gt = config.GROUND_TRUTH / f"ground_truth_outbreaks_{a.truth_tag}.csv"
            if not gt.exists():
                print("[evaluate_detection] no ground truth file for this run; skipped")
                continue
        ok, dur = run_stage(name, mk(a), a.retries, log)
        total += dur
        print(f"[{name}] {'ok' if ok else 'FAILED'} in {dur}s", flush=True)
        if not ok:
            sys.exit(f"stage {name} failed after retries")
        if name == "gold" and a.incremental and not a.force:
            import json
            lg = config.OUTPUTS / "last_gold_run.json"
            if lg.exists() and json.loads(lg.read_text()).get("weeks_rebuilt", 1) == 0:
                print("no new data since the last run: analytics and serving are already up to date (use --force to rerun)")
                break
    print(f"pipeline finished in {total:.1f}s")


if __name__ == "__main__":
    main()
