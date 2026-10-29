"""Seeded synthetic generator for S4 (encounters), S5 (registry), S6 (stock/beds) and ground truth.

Usage:
  python -m generator.generate --sf 0.05 --seed 42                 # history 2021-2025
  python -m generator.generate --drop --seed 99 --start 2026-01-01 --days 14   # hidden-style drop

Determinism: the same (seed, sf, dates) always produces identical files.
Ground-truth files are written to data/ground_truth/ and are read only by evaluation code.
"""
import argparse
import json
import numpy as np
import pandas as pd
from scipy.signal import lfilter
from common.config import RAW, GROUND_TRUTH, REFERENCE, DATA, ensure_dirs
from generator.weather import get_weather

DISEASES = ["dengue", "diarrhoea", "ili", "malaria", "other"]
YEARLY_AT_SF1 = {"dengue": 500_000, "diarrhoea": 1_000_000, "ili": 1_200_000,
                 "malaria": 150_000, "other": 2_150_000}          # 5.0M/yr -> 25M over 2021-2025
PEAK_DOY = {"dengue": 265, "diarrhoea": 130, "ili": 200, "malaria": 190, "other": 100}
AMPLITUDE = {"dengue": 1.3, "diarrhoea": 0.45, "ili": 0.6, "malaria": 0.9, "other": 0.08}
ICD = {"dengue": (["A90", "A91"], [0.7, 0.3]), "diarrhoea": (["A00", "A09"], [0.08, 0.92]),
       "ili": (["J10", "J11"], [0.35, 0.65]), "malaria": (["B50", "B51", "B52", "B53", "B54"],
                                                           [0.30, 0.55, 0.05, 0.05, 0.05]),
       "other": (["J06", "K29", "I10", "E11", "N39", "R50", "J18", "B34", "L03", "M54"], None)}
LAB_P = {"dengue": 0.40, "diarrhoea": 0.06, "ili": 0.10, "malaria": 0.80, "other": 0.02}
ADMIT_P = {"dengue": 0.20, "diarrhoea": 0.12, "ili": 0.06, "malaria": 0.25, "other": 0.03}
CFR = {"dengue": 0.005, "diarrhoea": 0.010, "ili": 0.020, "malaria": 0.010, "other": 0.005}
# approximate public holidays and Eid periods (teaching data; not an official calendar)
HOLIDAYS = pd.to_datetime(
    [f"{y}-{md}" for y in range(2020, 2028) for md in
     ("02-21", "03-26", "04-14", "05-01", "12-16", "12-25")] +
    ["2021-05-12", "2021-05-13", "2021-05-14", "2021-07-21", "2021-07-22", "2021-07-23",
     "2022-05-02", "2022-05-03", "2022-05-04", "2022-07-10", "2022-07-11", "2022-07-12",
     "2023-04-21", "2023-04-22", "2023-04-23", "2023-06-29", "2023-06-30", "2023-07-01",
     "2024-04-10", "2024-04-11", "2024-04-12", "2024-06-17", "2024-06-18", "2024-06-19",
     "2025-03-31", "2025-04-01", "2025-04-02", "2025-06-07", "2025-06-08", "2025-06-09",
     "2026-03-20", "2026-03-21", "2026-03-22", "2026-05-27", "2026-05-28", "2026-05-29"])


def load_reference():
    d = pd.read_csv(REFERENCE / "districts.csv")
    f = pd.read_csv(REFERENCE / "facilities.csv")
    return d, f


# ----------------------------------------------------------------------------- outbreaks
def make_outbreak_plan(rng, districts, start, end, n_local, nationwide, slow, prefix):
    """Return DataFrame of injected outbreaks (ground truth)."""
    names = districts["district"].to_numpy()
    pop = districts["population"].to_numpy(float)
    w_by = {"dengue": pop * districts["urban"].to_numpy(),
            "diarrhoea": pop * districts["flood"].to_numpy(),
            "ili": pop, "malaria": pop * districts["hill"].to_numpy()}
    mix = ["dengue"] * 12 + ["diarrhoea"] * 9 + ["ili"] * 6 + ["malaria"] * 5
    rows = []
    span = (pd.Timestamp(end) - pd.Timestamp(start)).days
    n_local = int(n_local)
    for i in range(n_local):
        dis = mix[i % len(mix)] if n_local >= len(mix) else rng.choice(mix)
        w = w_by[dis] / w_by[dis].sum()
        di = rng.choice(len(names), p=w)
        # dengue outbreaks are placed in the high season half of the time
        lo = 20 if prefix == "DROP" else 90
        s = pd.Timestamp(start) + pd.Timedelta(days=int(rng.integers(lo, max(lo + 1, span - 40))))
        if prefix == "DROP":
            s = pd.Timestamp(start) + pd.Timedelta(days=int(rng.integers(2, 8)))
        dur = int(rng.integers(28, 75)) if prefix != "DROP" else int(rng.integers(28, 42))
        peak = float(np.round(rng.uniform(3.0, 7.0), 2))
        rows.append(dict(outbreak_id=f"{prefix}-{i+1:03d}", outbreak_type="local", disease=dis,
                         district_id=districts["district_id"].iloc[di], start_date=s,
                         end_date=s + pd.Timedelta(days=dur), peak_multiplier=peak, shape="triangle"))
    if slow:
        di = int(np.argsort(pop)[len(pop) // 2])
        s = pd.Timestamp(start) + pd.Timedelta(days=int(span * 0.35))
        rows.append(dict(outbreak_id=f"{prefix}-SLOW", outbreak_type="slow_growing", disease="dengue",
                         district_id=districts["district_id"].iloc[di], start_date=s,
                         end_date=s + pd.Timedelta(days=110), peak_multiplier=2.6, shape="ramp"))
    if nationwide:
        ds = districts.copy()
        dh = ds[ds["district"] == "Dhaka"].iloc[0]
        dist = np.hypot(ds["lat"] - dh["lat"], ds["lon"] - dh["lon"]).to_numpy()
        lag = np.round(dist / dist.max() * 28).astype(int)             # spreads over four weeks
        s0 = pd.Timestamp("2024-03-04")
        for di in range(len(ds)):
            s = s0 + pd.Timedelta(days=int(lag[di]))
            rows.append(dict(outbreak_id=f"{prefix}-NAT", outbreak_type="nationwide_respiratory",
                             disease="ili", district_id=ds["district_id"].iloc[di], start_date=s,
                             end_date=s + pd.Timedelta(days=63), peak_multiplier=4.0, shape="triangle"))
    return pd.DataFrame(rows)


def outbreak_field(plan, days, districts):
    """Multiplier array [T, D, 5] (1 = no outbreak)."""
    T, D = len(days), len(districts)
    om = np.ones((T, D, len(DISEASES)))
    didx = {d: i for i, d in enumerate(districts["district_id"])}
    kidx = {k: i for i, k in enumerate(DISEASES)}
    t = (days - days[0]).days.to_numpy()
    for _, o in plan.iterrows():
        a = (pd.Timestamp(o["start_date"]) - days[0]).days
        b = (pd.Timestamp(o["end_date"]) - days[0]).days
        if b < 0 or a > t[-1] or b <= a:
            continue
        x = (t - a) / (b - a)
        if o["shape"] == "ramp":
            prof = np.where((x >= 0) & (x <= 1), x, 0.0)
        else:
            prof = np.where((x >= 0) & (x <= 0.35), x / 0.35,
                            np.where((x > 0.35) & (x <= 1), (1 - x) / 0.65, 0.0))
        om[:, didx[o["district_id"]], kidx[o["disease"]]] += prof * (o["peak_multiplier"] - 1)
    return om


# ----------------------------------------------------------------------------- intensity
def climate_multipliers(weather, districts, days):
    """Return rain/temp lag features for the simulated days (needs 70 days of history)."""
    D = len(districts)
    piv = lambda c: weather.pivot(index="date", columns="district_id", values=c)[districts["district_id"]]
    rain, temp = piv("precip_mm"), piv("temp_c")
    lag_r = rain.rolling(29, min_periods=15).mean().shift(28)
    lag_t = temp.rolling(29, min_periods=15).mean().shift(28)
    z = lambda x: ((x - x.mean()) / (x.std() + 1e-9)).fillna(0.0)
    r7 = rain.rolling(7, min_periods=1).sum()
    thr = r7.quantile(0.95)
    ext = (r7 > thr).astype(float)
    ext_decay = ext.copy()
    ext_decay.loc[:, :] = lfilter([1.0], [1.0, -0.93], ext.to_numpy(), axis=0)   # ~10 day half-life
    sel = lambda x: x.loc[days].to_numpy()
    return sel(z(lag_r)), sel(z(lag_t)), sel(ext_decay)


def build_intensity(rng, days, districts, weather, state, sf):
    T, D, K = len(days), len(districts), len(DISEASES)
    doy = days.dayofyear.to_numpy()[:, None]
    pop = districts["population"].to_numpy(float)
    phase = np.random.default_rng(7).uniform(-15, 15, D)[None, :]
    zr, zt, ext = climate_multipliers(weather, districts, days)
    share = np.zeros((D, K))
    share[:, 0] = pop * districts["urban"].to_numpy()
    share[:, 1] = pop * districts["flood"].to_numpy() ** 0.7
    share[:, 2] = pop
    share[:, 3] = pop * districts["hill"].to_numpy()
    share[:, 4] = pop
    share /= share.sum(0, keepdims=True)
    M = np.ones((T, D, K))
    for k, dis in enumerate(DISEASES):
        ang = 2 * np.pi * (doy - PEAK_DOY[dis] - phase) / 365.0
        M[:, :, k] = np.exp(AMPLITUDE[dis] * np.cos(ang))
    M[:, :, 0] *= np.exp(0.30 * np.clip(zr, -3, 3) + 0.20 * np.clip(zt, -3, 3))
    M[:, :, 1] *= (1 + 0.9 * ext) * np.exp(0.10 * np.clip(zt, -3, 3))
    M[:, :, 2] *= np.exp(-0.12 * np.clip(zt, -3, 3))
    M[:, :, 3] *= np.exp(0.25 * np.clip(zr, -3, 3))
    # persistent random level (AR(1) on the log scale)
    x = np.zeros((D, K))
    ar = np.zeros((T, D, K))
    for t in range(T):
        x = 0.92 * x + rng.normal(0, 0.05, (D, K))
        ar[t] = x
    M *= np.exp(ar - 0.5 * (0.05 ** 2 / (1 - 0.92 ** 2)))
    lam0 = share[None, :, :] * M
    if "norm" not in state:
        total_days = T
        state["norm"] = {dis: float(YEARLY_AT_SF1[dis] * sf * total_days / 365.0 / lam0[:, :, k].sum())
                         for k, dis in enumerate(DISEASES)}
    norm = np.array([state["norm"][d] for d in DISEASES])
    return lam0 * norm[None, None, :]


# ----------------------------------------------------------------------------- records
def report_dates(onset, delay, rng, weekend_report_p=0.15):
    rd = onset + pd.to_timedelta(delay, unit="D")
    wd = rd.dayofweek.to_numpy()                      # Fri=4, Sat=5 are the weekend
    shift = np.where(wd == 4, 2, np.where(wd == 5, 1, 0))
    move = (shift > 0) & (rng.random(len(rd)) > weekend_report_p)
    rd = rd + pd.to_timedelta(np.where(move, shift, 0), unit="D")
    hol = rd.isin(HOLIDAYS)
    rd = rd + pd.to_timedelta(np.where(hol & (rng.random(len(rd)) > 0.3), 1, 0), unit="D")
    return rd


def make_records(rng, day_idx, dist_idx, dis_idx, om_val, days, districts, fac, fac_by_d, fac_w_by_d,
                 prefix, chunk_no, defect_mult):
    n = len(day_idx)
    onset = days[day_idx]
    delay = np.minimum(21, np.floor(rng.gamma(1.3, 2.4, n))).astype(int)
    rdate = report_dates(onset, delay, rng)
    fidx = np.empty(n, dtype=np.int64)
    for d in np.unique(dist_idx):
        m = np.where(dist_idx == d)[0]
        fidx[m] = rng.choice(fac_by_d[d], size=len(m), p=fac_w_by_d[d])
    age = np.zeros(n)
    sex = np.where(rng.random(n) < 0.5, "M", "F").astype(object)
    icd = np.empty(n, dtype=object)
    lab = np.zeros(n, dtype=int); adm = np.zeros(n, dtype=int)
    outcome = np.empty(n, dtype=object)
    u1, u2, u3 = rng.random(n), rng.random(n), rng.random(n)
    for k, dis in enumerate(DISEASES):
        m = dis_idx == k
        c = int(m.sum())
        if c == 0:
            continue
        if dis == "dengue":
            a = rng.gamma(4.0, 6.5, c)
        elif dis == "diarrhoea":
            a = np.where(rng.random(c) < 0.55, rng.uniform(0, 5, c), rng.gamma(2.5, 16, c))
        elif dis == "ili":
            a = rng.gamma(2.2, 14, c)
        elif dis == "malaria":
            a = rng.gamma(5.0, 7.0, c)
        else:
            a = rng.gamma(2.5, 17, c)
        age[m] = np.clip(np.floor(a), 0, 100)
        codes, p = ICD[dis]
        icd[m] = rng.choice(codes, size=c, p=p)
        sex[m] = np.where(rng.random(c) < (0.6 if dis == "malaria" else 0.5), "M", "F")
        lab[m] = (u1[m] < np.minimum(0.95, LAB_P[dis] * (1 + 0.15 * np.log(om_val[m])))).astype(int)
        if dis == "diarrhoea":                       # cholera code more often confirmed
            lab[m & (icd == "A00")] = (u1[m & (icd == "A00")] < 0.55).astype(int)
        p_adm = np.minimum(0.6, ADMIT_P[dis] * (1 + 0.35 * np.log(om_val[m])))
        adm[m] = (u2[m] < p_adm).astype(int)
        died = adm[m] * (u3[m] < CFR[dis] * 4) + (1 - adm[m]) * (u3[m] < CFR[dis] * 0.1)
        ref = u3[m] > 0.96
        outcome[m] = np.where(died > 0, "died", np.where(ref, "referred",
                              np.where(u3[m] > 0.90, "under_treatment", "recovered")))
    pid = rng.integers(10 ** 9, 10 ** 10, n)
    eid = np.char.add(f"{prefix}{chunk_no:03d}", np.char.zfill(np.arange(n).astype(str), 8))
    df = pd.DataFrame(dict(
        encounter_id=eid, patient_id=pid.astype(str), facility_id=fac["facility_id"].to_numpy()[fidx],
        onset_date=onset, report_date=rdate, age_years=age.astype(int), sex=sex, icd10_code=icd,
        lab_confirmed=lab, admitted=adm, outcome=outcome))
    df["_fidx"] = fidx; df["_dis"] = dis_idx; df["_day"] = day_idx; df["_did"] = dist_idx
    return df


def cross_facility_duplicates(rng, df, fac, fac_by_d, frac=0.015):
    """Same patient seen at a second facility of the same district within 0-3 days."""
    cand = df[df["_dis"] < 4]
    m = cand.sample(frac=frac, random_state=int(rng.integers(1 << 30)))
    if len(m) == 0:
        return df.iloc[0:0].copy()
    cp = m.copy()
    newf = []
    for did, fi in zip(m["_did"].to_numpy(), m["_fidx"].to_numpy()):
        opts = [x for x in fac_by_d[did] if x != fi]
        newf.append(rng.choice(opts) if opts else fi)
    newf = np.array(newf)
    shift = rng.integers(0, 4, len(cp))
    cp["encounter_id"] = cp["encounter_id"].str.cat(pd.Series(["X"] * len(cp), index=cp.index))
    cp["facility_id"] = fac["facility_id"].to_numpy()[newf]
    cp["_fidx"] = newf
    cp["onset_date"] = cp["onset_date"] + pd.to_timedelta(shift, unit="D")
    cp["report_date"] = np.maximum(cp["report_date"] + pd.to_timedelta(shift, unit="D"), cp["onset_date"])
    cp["_orig"] = m["encounter_id"].to_numpy()
    return cp


def apply_defects(rng, df, mult):
    n = len(df)
    idx = lambda p: np.where(rng.random(n) < p * mult)[0]
    i = idx(0.008); df.loc[df.index[i], "age_years"] = rng.choice([212, -5, 150, 999], len(i))
    i = idx(0.002); df.loc[df.index[i], "sex"] = rng.choice(["X", "?"], len(i))
    i = idx(0.003); df.loc[df.index[i], "icd10_code"] = rng.choice(["Z99X", "A9", "XYZ", "A9O"], len(i))
    i = idx(0.002)
    df.loc[df.index[i], "onset_date"] = df["report_date"].iloc[i] + pd.to_timedelta(rng.integers(1, 6, len(i)), unit="D")
    i = idx(0.001); df.loc[df.index[i], "lab_confirmed"] = 2
    out = df.copy()
    out["onset_date"] = out["onset_date"].dt.strftime("%Y-%m-%d")
    out["report_date"] = out["report_date"].dt.strftime("%Y-%m-%d")
    i = idx(0.0005); out.iloc[i, out.columns.get_loc("onset_date")] = "2023-02-30"
    i = idx(0.005); out.iloc[i, out.columns.get_loc("facility_id")] = "F999" + pd.Series(rng.integers(100, 999, len(i)).astype(str)).to_numpy()
    i = idx(0.001); out.iloc[i, out.columns.get_loc("facility_id")] = ""
    return out


# ----------------------------------------------------------------------------- stock and beds
def stock_snapshots(days, fac, use, admits, state_stock, sf, write_mask, outdir):
    """Simulate stock per facility; use/admits are [T, F] arrays by resource."""
    F = len(fac)
    beds = fac["beds"].to_numpy(float)
    w = beds / beds.sum()
    yr = {k: YEARLY_AT_SF1[k] * sf for k in ("dengue", "diarrhoea", "malaria")}
    cap = {"iv_saline": w * (yr["dengue"] * 0.2 * 5 + yr["diarrhoea"] * 0.12 * 4) / 365 * 30 * 1.6,
           "ns1_kits": w * yr["dengue"] * 1.2 / 365 * 30 * 1.6,
           "ors": w * yr["diarrhoea"] * 5 / 365 * 30 * 1.6,
           "antimalarials": w * yr["malaria"] / 365 * 30 * 1.6}
    cap = {k: np.maximum(v, 1.0) for k, v in cap.items()}
    if "stock" not in state_stock:
        state_stock["stock"] = {k: cap[k] * 0.9 for k in cap}
        state_stock["admit_hist"] = np.zeros((6, F))
        state_stock["offset"] = (np.arange(F) * 7) % 14
        state_stock["day0"] = days[0]
    cur = state_stock["stock"]
    hist = state_stock["admit_hist"]
    rng = np.random.default_rng(int(days[0].strftime("%Y%m%d")) + 11)
    for t, day in enumerate(days):
        resup = ((day - state_stock["day0"]).days % 14 == state_stock["offset"])
        for k in cur:
            cur[k] = np.maximum(0.0, cur[k] - use[k][t])
            fill = cap[k] * 0.9 * rng.uniform(0.6, 1.0, F)
            cur[k] = np.where(resup, np.minimum(cap[k], cur[k] + fill), cur[k])
        hist = np.vstack([hist[1:], admits[t]])
        occ = np.minimum(beds, np.round(0.55 * beds * (1 + 0.1 * np.sin(day.dayofyear / 58.0))
                                       + hist.sum(0) * 1.0)).astype(int)
        if write_mask[t]:
            snap = pd.DataFrame(dict(date=day.strftime("%Y-%m-%d"), facility_id=fac["facility_id"],
                                     iv_saline=cur["iv_saline"].round().astype(int),
                                     ns1_kits=cur["ns1_kits"].round().astype(int),
                                     ors=cur["ors"].round().astype(int),
                                     antimalarials=cur["antimalarials"].round().astype(int),
                                     occupied_beds=occ))
            snap.to_json(outdir / f"stock_{day:%Y%m%d}.jsonl", orient="records", lines=True)
    state_stock["admit_hist"] = hist


# ----------------------------------------------------------------------------- driver
def simulate(seed, sf, start, end, write_start, write_end, drop=False, n_local=32,
             defect_mult=1.0, artefact=None):
    ensure_dirs()
    rng = np.random.default_rng(seed)
    districts, fac = load_reference()
    D, F = len(districts), len(fac)
    prefix = "D" if drop else "E"
    enc_dir = RAW / "encounters"; stock_dir = RAW / "stock"
    for p in (enc_dir, stock_dir, RAW / "reference", GROUND_TRUTH):
        p.mkdir(parents=True, exist_ok=True)
    state_path = DATA / "generator_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if not drop:
        state = {}
    days_all = pd.date_range(start, end, freq="D")
    weather = get_weather(districts, pd.Timestamp(start) - pd.Timedelta(days=75), end, seed)
    lam = build_intensity(rng, days_all, districts, weather, state, sf)
    plan = make_outbreak_plan(rng, districts, write_start, end, n_local, nationwide=not drop,
                              slow=not drop, prefix="DROP" if drop else "OB")
    om = outbreak_field(plan, days_all, districts)
    counts = rng.poisson(lam * om * rng.gamma(6.0, 1 / 6.0, lam.shape))
    fac_by_d, fac_w_by_d = {}, {}
    for di, did in enumerate(districts["district_id"]):
        ids = np.where(fac["district_id"].to_numpy() == did)[0]
        w = fac["beds"].to_numpy(float)[ids] ** 0.8
        fac_by_d[di], fac_w_by_d[di] = ids, w / w.sum()
    # reporting artefact: a facility silent, then back-fills on one day
    if artefact is None:
        artefact = dict(silent_start=pd.Timestamp("2024-07-08"), silent_days=21)
    mid = districts.sort_values("population").index[len(districts) // 2]
    afi = fac_by_d[mid][int(np.argmax(fac_w_by_d[mid]))]
    a0 = pd.Timestamp(artefact["silent_start"]); a1 = a0 + pd.Timedelta(days=artefact["silent_days"])
    art = pd.DataFrame([dict(facility_id=fac["facility_id"].iloc[afi], district_id=districts["district_id"].iloc[mid],
                             silent_start=a0, silent_end=a1 - pd.Timedelta(days=1), backfill_date=a1)])
    buf, dup_rows = [], []
    out_days = pd.date_range(write_start, write_end, freq="D")
    total_rows = 0
    # chunk by calendar month of onset
    month_edges = pd.date_range(days_all[0].replace(day=1), days_all[-1] + pd.offsets.MonthBegin(1), freq="MS")
    use = {k: np.zeros((len(days_all), F)) for k in ("iv_saline", "ns1_kits", "ors", "antimalarials")}
    admits = np.zeros((len(days_all), F))
    for ci in range(len(month_edges) - 1):
        a, b = month_edges[ci], month_edges[ci + 1]
        sel = np.where((days_all >= a) & (days_all < b))[0]
        if len(sel) == 0:
            continue
        c = counts[sel]                                   # [t, d, k]
        t_i, d_i, k_i = np.nonzero(c)
        rep = c[t_i, d_i, k_i]
        day_idx = np.repeat(sel[t_i], rep); dist_idx = np.repeat(d_i, rep); dis_idx = np.repeat(k_i, rep)
        om_val = np.repeat(om[sel][t_i, d_i, k_i], rep)
        if len(day_idx) == 0:
            continue
        df = make_records(rng, day_idx, dist_idx, dis_idx, om_val, days_all, districts, fac, fac_by_d,
                          fac_w_by_d, prefix + (str(seed % 10) if drop else ""), ci, defect_mult)
        # stock consumption by onset day and facility
        flat = df["_day"].to_numpy() * F + df["_fidx"].to_numpy()
        def acc(mask, w=1.0):
            return np.bincount(flat[mask], minlength=len(days_all) * F).reshape(len(days_all), F) * w
        k = df["_dis"].to_numpy(); adm = df["admitted"].to_numpy() == 1
        use["iv_saline"] += acc(adm & (k == 0), 5.0) + acc(adm & (k == 1), 4.0)
        use["ns1_kits"] += acc(k == 0, 1.2)
        use["ors"] += acc(k == 1, 5.0)
        use["antimalarials"] += acc(k == 3, 1.0)
        admits += acc(adm)
        # resubmitted duplicates (same encounter_id) and cross-facility patient duplicates
        resub = df.sample(frac=0.02 * defect_mult, random_state=int(rng.integers(1 << 30))).copy()
        resub["report_date"] = resub["report_date"] + pd.to_timedelta(rng.integers(1, 11, len(resub)), unit="D")
        xdup = cross_facility_duplicates(rng, df, fac, fac_by_d, frac=0.015)
        if len(xdup):
            dup_rows.append(xdup[["encounter_id", "_orig"]].rename(columns={"encounter_id": "duplicate_encounter_id",
                                                                           "_orig": "original_encounter_id"}))
        full = pd.concat([df, resub, xdup.drop(columns=["_orig"])], ignore_index=True)
        # artefact: records of the silent facility are reported on the back-fill day
        if not drop or artefact.get("apply_in_drop", True):
            m = (full["facility_id"] == art["facility_id"].iloc[0]) & (full["report_date"] >= a0) & (full["report_date"] < a1)
            full.loc[m, "report_date"] = a1
        full = apply_defects(rng, full, defect_mult)
        buf.append(full)
        cutoff = b                                         # all report days before the next chunk are complete
        allb = pd.concat(buf, ignore_index=True)
        rd = pd.to_datetime(allb["report_date"], errors="coerce")
        done = (rd < cutoff) | (ci == len(month_edges) - 2)
        total_rows += _write_days(allb[done], rd[done], enc_dir, write_start, write_end)
        buf = [allb[~done]]
    # stock and bed snapshots for the written days
    state_stock = {}
    wm = (days_all >= pd.Timestamp(write_start)) & (days_all <= pd.Timestamp(write_end))
    stock_snapshots(days_all, fac, use, admits, state_stock, sf, wm, stock_dir)
    # reference files landed as sources S1, S2, S5
    if not drop:
        districts.to_csv(RAW / "reference" / "districts.csv", index=False)
        fac.to_csv(RAW / "reference" / "facility_registry.csv", index=False)
        state_path.write_text(json.dumps(state))
    # ground truth (evaluation only)
    tag = "drop" if drop else "history"
    plan.assign(start_date=plan["start_date"].dt.strftime("%Y-%m-%d"),
                end_date=plan["end_date"].dt.strftime("%Y-%m-%d")).to_csv(
        GROUND_TRUTH / f"ground_truth_outbreaks_{tag}.csv", index=False)
    art.assign(**{c: art[c].dt.strftime("%Y-%m-%d") for c in ("silent_start", "silent_end", "backfill_date")}).to_csv(
        GROUND_TRUTH / f"ground_truth_artefacts_{tag}.csv", index=False)
    if dup_rows:
        pd.concat(dup_rows).to_csv(GROUND_TRUTH / f"ground_truth_duplicates_{tag}.csv", index=False)
    return dict(rows_written=int(total_rows), outbreaks=int(plan["outbreak_id"].nunique()), files=len(list(enc_dir.glob("*.csv"))))


def _write_days(df, rd, enc_dir, write_start, write_end):
    n = 0
    lo, hi = pd.Timestamp(write_start), pd.Timestamp(write_end)
    ok = (rd >= lo) & (rd <= hi)
    cols = ["encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "age_years", "sex",
            "icd10_code", "lab_confirmed", "admitted", "outcome"]
    for day, g in df[ok].groupby(rd[ok]):
        g[cols].sort_values("encounter_id").to_csv(enc_dir / f"encounters_{day:%Y%m%d}.csv", index=False)
        n += len(g)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sf", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--drop", action="store_true")
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    if a.drop:
        ws = pd.Timestamp(a.start); we = ws + pd.Timedelta(days=a.days - 1)
        res = simulate(a.seed, a.sf, ws - pd.Timedelta(days=25), we, ws, we, drop=True, n_local=4,
                       defect_mult=2.0, artefact=dict(silent_start=ws, silent_days=7))
    else:
        res = simulate(a.seed, a.sf, a.start, a.end, a.start, a.end)
    print(json.dumps(res))


if __name__ == "__main__":
    main()
