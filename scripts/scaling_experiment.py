"""ENG-2: run the full pipeline at several scale factors and record runtime per stage, sizes and hardware.

Usage: python scripts/scaling_experiment.py 0.25 0.5 1.0
Each scale factor runs in its own data directory (data_scaling/sf_<SF>) so runs do not interfere.
Results: docs/scalability_results.csv and docs/scalability_results.md
"""
import csv
import os
import pathlib
import platform
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def du(path):
    return sum(f.stat().st_size for f in pathlib.Path(path).rglob("*") if f.is_file()) if pathlib.Path(path).exists() else 0


def hardware():
    mem = next((int(l.split()[1]) // 1024 for l in open("/proc/meminfo") if l.startswith("MemTotal")), 0) if os.path.exists("/proc/meminfo") else 0
    java = subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.splitlines()[0]
    return dict(cpus=os.cpu_count(), memory_mb=mem, python=platform.python_version(), java=java, os=platform.platform())


def main():
    sfs = [float(a) for a in sys.argv[1:]] or [0.25, 0.5, 1.0]
    rows = []
    import json
    part = ROOT / "docs" / "scalability_partial"
    part.mkdir(parents=True, exist_ok=True)
    for sf in sfs:
        done = part / f"sf_{sf}.json"
        if done.exists():                       # resume after an interrupted run
            rows.append(json.loads(done.read_text()))
            continue
        data = ROOT / "data_scaling" / f"sf_{sf}"
        env = dict(os.environ, EPISCOPE_DATA=str(data), PYTHONPATH=str(ROOT))
        if not env.get("EPISCOPE_SALT"):
            env["EPISCOPE_SALT"] = "scaling-experiment-salt"
        subprocess.run(["rm", "-rf", str(data)])
        r = subprocess.run([sys.executable, "-m", "orchestration.run_pipeline", "--sf", str(sf), "--seed", "42"] + (["--stages", os.environ["SCALING_STAGES"]] if os.environ.get("SCALING_STAGES") else []), cwd=ROOT, env=env)
        log = data / "logs" / "stage_times.csv"
        times = {}
        if log.exists():
            for row in csv.DictReader(open(log)):
                times[row["stage"]] = float(row["seconds"])
        rows.append(dict(sf=sf, status="ok" if r.returncode == 0 else "failed", total_seconds=round(sum(times.values()), 1),
                         **{f"t_{k}": v for k, v in times.items()},
                         raw_mb=round(du(data / "raw") / 1e6, 1), bronze_mb=round(du(data / "lake" / "bronze") / 1e6, 1),
                         silver_mb=round(du(data / "lake" / "silver") / 1e6, 1), gold_mb=round(du(data / "lake" / "gold") / 1e6, 1),
                         dashboard_json_mb=round(du(ROOT / "dashboard" / "data") / 1e6, 2)))
        done.write_text(json.dumps(rows[-1]))
    keys = sorted({k for r in rows for k in r}, key=lambda k: (not k.startswith(("sf", "status", "total")), k))
    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    with open(out / "scalability_results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
    hw = hardware()
    md = ["Hardware: " + ", ".join(f"{k}={v}" for k, v in hw.items()), "", "| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    md += ["| " + " | ".join(str(r.get(k, "")) for k in keys) + " |" for r in rows]
    (out / "scalability_results.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
