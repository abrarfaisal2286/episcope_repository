"""Builds small reference tables: districts (S1/S2) and the facility registry (S5).

District geometry comes from the OCHA COD-AB ADM2 boundaries. Populations below are
APPROXIMATE rounded district totals (thousands) entered for teaching purposes. Replace
data/reference/districts.csv::population with the official BBS Census 2022 table or
WorldPop sums before drawing any real-world conclusion.
"""
import numpy as np
import pandas as pd

# name (as in the boundary file) -> population in thousands (approximate)
POP_K = {
    "Bagerhat": 1508, "Bandarban": 482, "Barguna": 892, "Barisal": 2325, "Bhola": 1776,
    "Bogra": 3734, "Brahamanbaria": 3402, "Chandpur": 2688, "Chittagong": 9170,
    "Chuadanga": 1236, "Comilla": 6212, "Cox's Bazar": 2823, "Dhaka": 14734,
    "Dinajpur": 3315, "Faridpur": 2160, "Feni": 1544, "Gaibandha": 2379, "Gazipur": 5263,
    "Gopalganj": 1300, "Habiganj": 2358, "Jamalpur": 2438, "Jessore": 3100,
    "Jhalokati": 625, "Jhenaidah": 1877, "Joypurhat": 987, "Khagrachhari": 714,
    "Khulna": 2613, "Kishoreganj": 3200, "Kurigram": 2147, "Kushtia": 2138,
    "Lakshmipur": 1929, "Lalmonirhat": 1284, "Madaripur": 1204, "Magura": 950,
    "Manikganj": 1558, "Maulvibazar": 2010, "Meherpur": 705, "Munshiganj": 1520,
    "Mymensingh": 5900, "Naogaon": 2800, "Narail": 760, "Narayanganj": 3990,
    "Narsingdi": 2500, "Natore": 1900, "Nawabganj": 1800, "Netrakona": 2500,
    "Nilphamari": 2100, "Noakhali": 3700, "Pabna": 2900, "Panchagarh": 1200,
    "Patuakhali": 1700, "Pirojpur": 1200, "Rajbari": 1200, "Rajshahi": 2900,
    "Rangamati": 700, "Rangpur": 3100, "Satkhira": 2200, "Shariatpur": 1300,
    "Sherpur": 1500, "Sirajganj": 3300, "Sunamganj": 2700, "Sylhet": 3600,
    "Tangail": 3900, "Thakurgaon": 1500,
}
# Urban share driver for dengue (large cities), hill factor for malaria, flood factor for diarrhoea.
URBAN = {"Dhaka": 3.0, "Gazipur": 2.2, "Narayanganj": 2.2, "Chittagong": 2.4, "Khulna": 1.5,
         "Rajshahi": 1.3, "Sylhet": 1.3, "Barisal": 1.2, "Rangpur": 1.1, "Comilla": 1.2,
         "Mymensingh": 1.2, "Bogra": 1.1, "Jessore": 1.1, "Cox's Bazar": 1.3}
HILL = {"Bandarban": 8.0, "Rangamati": 6.0, "Khagrachhari": 6.0, "Cox's Bazar": 2.5,
        "Sylhet": 0.8, "Maulvibazar": 0.6, "Sunamganj": 0.4, "Habiganj": 0.3}
FLOOD = {"Sunamganj": 1.6, "Sylhet": 1.5, "Kurigram": 1.5, "Gaibandha": 1.4, "Sirajganj": 1.4,
         "Jamalpur": 1.4, "Bhola": 1.3, "Patuakhali": 1.3, "Barguna": 1.3, "Satkhira": 1.2,
         "Khulna": 1.2, "Bagerhat": 1.2, "Netrakona": 1.3, "Kishoreganj": 1.3}
RAIN_FACTOR = {"Sylhet": 1.9, "Sunamganj": 1.9, "Maulvibazar": 1.7, "Cox's Bazar": 1.8,
               "Bandarban": 1.5, "Rangamati": 1.5, "Khagrachhari": 1.4, "Chittagong": 1.5,
               "Rajshahi": 0.7, "Nawabganj": 0.7, "Natore": 0.75, "Naogaon": 0.8,
               "Chuadanga": 0.8, "Meherpur": 0.8, "Kushtia": 0.8, "Jessore": 0.9}


def build_districts(geojson_path, out_csv):
    import json
    from shapely.geometry import shape
    gj = json.load(open(geojson_path))
    rows = []
    for f in gj["features"]:
        p = f["properties"]
        name = p["ADM2_EN"]
        c = shape(f["geometry"]).representative_point()
        cen = shape(f["geometry"]).centroid
        rows.append(dict(
            district_id=p["ADM2_PCODE"], district=name, division=p["ADM1_EN"],
            lat=round(cen.y, 4), lon=round(cen.x, 4),
            population=int(POP_K[name] * 1000),
            urban=URBAN.get(name, 1.0), hill=HILL.get(name, 0.05),
            flood=FLOOD.get(name, 1.0), rain_factor=RAIN_FACTOR.get(name, 1.0)))
    df = pd.DataFrame(rows).sort_values("district_id").reset_index(drop=True)
    df.to_csv(out_csv, index=False)
    return df


def build_facilities(districts: pd.DataFrame, out_csv, n_total=640, seed=7):
    """Synthetic facility registry (S5): district hospitals, upazila health complexes, clinics."""
    rng = np.random.default_rng(seed)
    pop = districts["population"].to_numpy(float)
    n_per = np.maximum(4, np.round(n_total * pop / pop.sum()).astype(int))
    rows = []
    for (_, d), n in zip(districts.iterrows(), n_per):
        n_hosp = 1 + int(d["population"] > 4_000_000) + int(d["population"] > 9_000_000) * 2
        n_uhc = max(1, int(round(n * 0.35)))
        n_clinic = max(1, n - n_hosp - n_uhc)
        kinds = ["hospital"] * n_hosp + ["uhc"] * n_uhc + ["clinic"] * n_clinic
        for i, kind in enumerate(kinds):
            beds = {"hospital": int(rng.integers(150, 500)), "uhc": int(rng.integers(31, 60)),
                    "clinic": int(rng.integers(8, 20))}[kind]
            icu = int(round(beds * 0.05)) if kind == "hospital" else 0
            rows.append(dict(
                facility_id=f"F{d['district_id'][2:]}{i:03d}", district_id=d["district_id"],
                facility_type=kind, beds=beds, icu_beds=icu,
                lat=round(d["lat"] + rng.normal(0, 0.05), 4),
                lon=round(d["lon"] + rng.normal(0, 0.05), 4)))
    df = pd.DataFrame(rows)
    df.to_csv(out_csv, index=False)
    return df
