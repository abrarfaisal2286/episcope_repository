"""Central configuration: paths, environment variables and shared constants."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_env_file(path: pathlib.Path) -> None:
    """Minimal .env reader (no external dependency). Existing variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(ROOT / ".env")

DATA = pathlib.Path(os.environ.get("EPISCOPE_DATA", ROOT / "data"))
REFERENCE = ROOT / "data" / "reference"          # small committed reference data
RAW = DATA / "raw"                               # landing zone (generator output)
GROUND_TRUTH = DATA / "ground_truth"             # evaluation only, never read by pipeline
LAKE = DATA / "lake"
BRONZE = LAKE / "bronze"
SILVER = LAKE / "silver"
GOLD = LAKE / "gold"
LEDGER = LAKE / "_ledger"
OUTPUTS = DATA / "outputs"                       # analytics outputs (small)
SERVING = ROOT / "dashboard" / "data"            # suppressed JSON read by D3 dashboard
LOGS = DATA / "logs"

SALT = os.environ.get("EPISCOPE_SALT", "")
SPARK_MASTER = os.environ.get("SPARK_MASTER", "local[*]")
SPARK_DRIVER_MEMORY = os.environ.get("SPARK_DRIVER_MEMORY", "2g")
SHUFFLE_PARTITIONS = os.environ.get("SHUFFLE_PARTITIONS", "8")

DISEASES = ["dengue", "diarrhoea", "ili", "malaria"]
ICD_TO_DISEASE = {
    "A90": "dengue", "A91": "dengue",
    "A00": "diarrhoea", "A09": "diarrhoea",
    "J10": "ili", "J11": "ili",
    "B50": "malaria", "B51": "malaria", "B52": "malaria",
    "B53": "malaria", "B54": "malaria",
}
MAX_REPORT_DELAY_DAYS = 60       # sanity limit used by the quality rules
SMALL_CELL_MAX = 4               # counts 1..4 are suppressed in all outputs


def ensure_dirs() -> None:
    for p in (RAW, GROUND_TRUTH, BRONZE, SILVER, GOLD, LEDGER, OUTPUTS, SERVING, LOGS):
        p.mkdir(parents=True, exist_ok=True)
