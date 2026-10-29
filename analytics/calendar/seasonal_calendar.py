"""Seasonal preparedness calendar per division and disease group (AN-9) using STL decomposition."""
import datetime as dt
import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL
from common import config
from common.io import read_table
from common.epi import epi_week_start

LEAD_PREPOSITION_WEEKS = 6        # supplies must be in place this long before the season starts
LEAD_CAMPAIGN_WEEKS = 8           # prevention campaign starts this long before the season starts


def season_window(profile: np.ndarray):
    """Circular window of weeks above mean + 0.5 sd containing the peak."""
    thr = profile.mean() + 0.5 * profile.std()
    above = profile >= thr
    peak = int(np.argmax(profile))
    n = len(profile)
    s = peak
    while above[(s - 1) % n] and (peak - s) % n < n - 1:
        s = (s - 1) % n
    e = peak
    while above[(e + 1) % n] and (e - peak) % n < n - 1:
        e = (e + 1) % n
    return s, peak, e


def build() -> pd.DataFrame:
    w = read_table("gold", "weekly_cases")
    d = pd.read_csv(config.REFERENCE / "districts.csv")
    w = w.merge(d[["district_id", "division"]].rename(columns={"division": "div2"}), on="district_id")
    pop = d.groupby("division")["population"].sum()
    w["week_start"] = pd.to_datetime(w["week_start"])
    last_full = w["week_start"].max() - pd.Timedelta(weeks=5)                    # exclude immature weeks
    w = w[w["week_start"] <= last_full]
    next_year = int(w["epi_year"].max()) + 1
    rows = []
    for (div, dis), g in w[w["disease_group"].isin(config.DISEASES)].groupby(["div2", "disease_group"]):
        s = g.groupby("week_start")["cases"].sum().sort_index()
        inc = s / pop[div] * 100000
        if len(inc) < 110:
            continue
        stl = STL(inc.to_numpy(), period=52, robust=True, seasonal=13).fit()
        seas = pd.Series(stl.seasonal, index=inc.index)
        ew = [min(52, int(np.ceil((ws + pd.Timedelta(days=3)).dayofyear / 7))) for ws in inc.index]
        prof = pd.Series(seas.to_numpy()).groupby(ew).mean().reindex(range(1, 53)).interpolate().to_numpy()
        level = float(np.mean(inc.to_numpy()))
        strength = float(max(0.0, 1 - np.var(stl.resid) / np.var(stl.seasonal + stl.resid)))
        s0, pk, e0 = season_window(prof)
        marked = strength >= 0.3 and (prof.max() - prof.min()) > 0.25 * level
        start_w, peak_w, end_w = s0 + 1, pk + 1, e0 + 1
        pre_w = ((start_w - 1 - LEAD_PREPOSITION_WEEKS) % 52) + 1
        cam_w = ((start_w - 1 - LEAD_CAMPAIGN_WEEKS) % 52) + 1
        def dstr(wk, base=next_year):
            return epi_week_start(base, wk).isoformat()
        rows.append(dict(division=div, disease_group=dis, seasonal_strength=round(strength, 3),
                         mean_weekly_incidence_per_100k=round(level, 3),
                         peak_to_trough_ratio=round(float(min(50.0, (level + prof.max()) / max(level + prof.min(), 0.05 * level))), 2),
                         marked_season=bool(marked), peak_week=peak_w, season_start_week=start_w, season_end_week=end_w,
                         preposition_week=pre_w, campaign_start_week=cam_w,
                         peak_date=dstr(peak_w), season_start_date=dstr(start_w), season_end_date=dstr(end_w),
                         preposition_date=dstr(pre_w, next_year - (1 if pre_w > start_w else 0)),
                         campaign_start_date=dstr(cam_w, next_year - (1 if cam_w > start_w else 0)),
                         profile=[round(float(v), 4) for v in prof]))
    cal = pd.DataFrame(rows)
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    cal.drop(columns=["profile"]).to_csv(config.OUTPUTS / "preparedness_calendar.csv", index=False)
    cal.to_json(config.OUTPUTS / "preparedness_calendar.json", orient="records")
    return cal


if __name__ == "__main__":
    print(build().drop(columns=["profile"]).to_string())
