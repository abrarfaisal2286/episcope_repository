"""ING-6 latency test: copy freshly 'arriving' files into the stream landing directory, run the Structured
Streaming job once, and measure the delay from file arrival to the alert table being written.
Writes docs/streaming_result.json.  Usage: python scripts/streaming_test.py [n_files]"""
import json
import pathlib
import shutil
import subprocess
import sys
import pandas as pd
from common import config
from common.io import read_table

n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
src = sorted((config.RAW / "encounters").glob("encounters_*.csv"))[-n:]
dst = config.RAW / "encounters_stream"
shutil.rmtree(dst, ignore_errors=True); dst.mkdir(parents=True)
shutil.rmtree(config.LAKE / "_checkpoints" / "alerts_live", ignore_errors=True)
shutil.rmtree(config.GOLD / "alerts_live", ignore_errors=True)
records = 0
for f in src:                                     # copy gives each file a fresh modification time (arrival)
    shutil.copy(f, dst / f.name)
    records += sum(1 for _ in open(f)) - 1
r = subprocess.run([sys.executable, "-m", "pipeline.streaming.stream_alerts", "--once"], cwd=config.ROOT, capture_output=True, text=True)
if r.returncode != 0:
    print(r.stdout[-1500:], r.stderr[-1500:]); raise SystemExit("streaming job failed")
t = read_table("gold", "alerts_live")
res = dict(files=len(src), records=records, alert_rows=int(len(t)), provisional_alerts=int(t["provisional_alert"].fillna(False).sum()),
           median_latency_s=float(t["latency_seconds"].median()), max_latency_s=float(t["latency_seconds"].max()))
(pathlib.Path(__file__).resolve().parents[1] / "docs" / "streaming_result.json").write_text(json.dumps(res, indent=1))
print(res)
