"""Daily weather per district (S3).

Real data: fetch_open_meteo() queries the Open-Meteo historical archive at district centroids and
caches each response (JSON) under data/raw/weather/cache/. Fallback: a seeded synthetic monsoon
model, labelled source='synthetic_fallback', used when the cache does not cover the requested dates
(for example when the API is unreachable).
"""
import json
import time
import urllib.parse
import urllib.request
import numpy as np
import pandas as pd
from common.config import RAW

WEATHER_DIR = RAW / "weather"
CACHE_DIR = WEATHER_DIR / "cache"
API = "https://archive-api.open-meteo.com/v1/archive"
DAILY = "precipitation_sum,temperature_2m_mean,relative_humidity_2m_mean"


def fetch_open_meteo(districts: pd.DataFrame, start: str, end: str, pause: float = 1.5) -> int:
    """Download and cache one JSON per district. Returns number of districts fetched."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for _, d in districts.iterrows():
        out = CACHE_DIR / f"{d['district_id']}_{start}_{end}.json"
        if out.exists():
            continue
        q = urllib.parse.urlencode(dict(latitude=d["lat"], longitude=d["lon"], start_date=start,
                                        end_date=end, daily=DAILY, timezone="Asia/Dhaka"))
        for attempt in range(4):
            try:
                with urllib.request.urlopen(f"{API}?{q}", timeout=60) as r:
                    out.write_bytes(r.read())
                n += 1
                break
            except Exception as exc:  # network or rate limit: back off and retry
                time.sleep(pause * (attempt + 2))
                if attempt == 3:
                    raise RuntimeError(f"Open-Meteo failed for {d['district']}: {exc}")
        time.sleep(pause)
    return n


def load_cached(districts: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for f in sorted(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []:
        did = f.name.split("_")[0]
        j = json.loads(f.read_text())["daily"]
        frames.append(pd.DataFrame(dict(
            date=pd.to_datetime(j["time"]), district_id=did,
            precip_mm=j["precipitation_sum"], temp_c=j["temperature_2m_mean"],
            humidity_pct=j["relative_humidity_2m_mean"], source="open_meteo")))
    if not frames:
        return pd.DataFrame(columns=["date", "district_id", "precip_mm", "temp_c", "humidity_pct", "source"])
    return pd.concat(frames, ignore_index=True)


def synthetic_weather(districts: pd.DataFrame, start, end, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.date_range(start, end, freq="D")
    T, D = len(days), len(districts)
    doy = days.dayofyear.to_numpy()
    monsoon = np.exp(-(((doy - 200) / 55.0) ** 2))                      # peak mid-July
    divs = {v: i for i, v in enumerate(sorted(districts["division"].unique()))}
    div_idx = districts["division"].map(divs).to_numpy()
    # regional storm factor: AR(1) shared by division
    z = np.zeros((T, len(divs)))
    for t in range(1, T):
        z[t] = 0.7 * z[t - 1] + rng.normal(0, 0.7, len(divs))
    wet_p = np.clip(0.12 + 0.65 * monsoon, 0, 0.9)[:, None] * np.ones((1, D))
    mean_amt = (2.5 + 17.0 * monsoon)[:, None] * districts["rain_factor"].to_numpy()[None, :]
    reg = np.exp(0.5 * z[:, div_idx])
    wet = rng.random((T, D)) < np.clip(wet_p * reg ** 0.5, 0, 0.95)
    amt = rng.gamma(0.8, 1.0, (T, D)) * mean_amt / 0.8 * reg
    rain = np.where(wet, amt, 0.0)
    ext = rng.random((T, len(divs))) < (0.004 * monsoon[:, None] + 0.0005)   # rare extreme events
    rain += ext[:, div_idx] * rng.uniform(60, 220, (T, D)) * (rng.random((T, D)) < 0.6)
    lat = districts["lat"].to_numpy()
    temp = (24.5 + 5.0 * np.sin(2 * np.pi * (doy - 105) / 365.0))[:, None] - 0.6 * (lat - 23.5)[None, :]
    temp = temp - 0.03 * np.minimum(rain, 60) + rng.normal(0, 0.9, (T, D))
    hum = 62 + 20 * monsoon[:, None] + 0.12 * np.minimum(rain, 80) + rng.normal(0, 4, (T, D))
    out = pd.DataFrame({
        "date": np.repeat(days.values, D),
        "district_id": np.tile(districts["district_id"].to_numpy(), T),
        "precip_mm": rain.ravel().round(1), "temp_c": temp.ravel().round(1),
        "humidity_pct": np.clip(hum, 25, 99).ravel().round(0), "source": "synthetic_fallback"})
    return out


def get_weather(districts: pd.DataFrame, start, end, seed: int, write: bool = True) -> pd.DataFrame:
    """Return weather for [start, end]; prefer cached real data, fill gaps synthetically.

    Newly synthesised days are written to data/raw/weather/ as a landing file so that the
    pipeline ingests exactly the weather the generator used.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    have = []
    real = load_cached(districts)
    if len(real):
        have.append(real)
    for f in sorted(WEATHER_DIR.glob("weather_*.csv")):
        have.append(pd.read_csv(f, parse_dates=["date"]))
    all_w = pd.concat(have, ignore_index=True) if have else None
    if all_w is not None:
        all_w = all_w.drop_duplicates(["date", "district_id"], keep="first")
    days = pd.date_range(start, end, freq="D")
    missing = days if all_w is None else days.difference(pd.DatetimeIndex(all_w["date"].unique()))
    if len(missing):
        syn = synthetic_weather(districts, missing.min(), missing.max(), seed + int(missing.min().strftime("%y%m%d")))
        syn = syn[syn["date"].isin(missing)]
        if write:
            syn.assign(date=syn["date"].dt.strftime("%Y-%m-%d")).to_csv(
                WEATHER_DIR / f"weather_{missing.min():%Y%m%d}_{missing.max():%Y%m%d}.csv", index=False)
        all_w = syn if all_w is None else pd.concat([all_w, syn], ignore_index=True)
    all_w = all_w[(all_w["date"] >= start) & (all_w["date"] <= end)]
    return all_w.sort_values(["date", "district_id"]).reset_index(drop=True)
