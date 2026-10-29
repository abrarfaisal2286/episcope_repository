"""Allocation optimiser (AN-7, AN-8): linear programme solved with SciPy HiGHS.

min  sum_{d,r} w_d * c_r * u_{d,r}  +  eps * sum c_r x_{d,r}
s.t. u_{d,r} + x_{d,r} >= need_{d,r}                      (unmet demand)
     sum c_r x_{d,r} <= B                                  (national budget)
     x_{d,r} >= alpha * p_d * sum_{d'} x_{d',r}            (equity floor)
     sum_{d in k, r} v_r x_{d,r} <= T_k                    (division transport capacity)
Unmet demand is valued in money (w_d * c_r) so that different resources are comparable.
"""
import json
import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy import sparse
from common import config
from analytics.optimise.demand import ASSUME, RESOURCES

COST = np.array([ASSUME["resources"][r]["cost_usd"] for r in RESOURCES])
VOL = np.array([ASSUME["resources"][r]["volume"] for r in RESOURCES])
EPS = 1e-4


def solve(need, w, p, div_idx, budget, alpha, tcap):
    """need: [D,R]; w,p: [D]; div_idx: [D] ints; returns x [D,R] or raises."""
    D, R = need.shape
    nv = D * R
    c = np.concatenate([EPS * np.tile(COST, D), (w[:, None] * COST[None, :]).ravel()])
    rows, cols, vals, b = [], [], [], []
    k = 0
    for i in range(nv):                                   # -x - u <= -need
        rows += [k, k]; cols += [i, nv + i]; vals += [-1.0, -1.0]; b.append(-need.ravel()[i]); k += 1
    for i in range(nv):                                   # budget
        rows.append(k); cols.append(i); vals.append(COST[i % R])
    b.append(budget); k += 1
    if alpha > 0:                                         # equity floor per resource
        for r in range(R):
            for d in range(D):
                rows.append(k); cols.append(d * R + r); vals.append(-1.0)
                for d2 in range(D):
                    rows.append(k); cols.append(d2 * R + r); vals.append(alpha * p[d])
                b.append(0.0); k += 1
    for kk in np.unique(div_idx):                         # transport capacity
        for d in np.where(div_idx == kk)[0]:
            for r in range(R):
                rows.append(k); cols.append(d * R + r); vals.append(VOL[r])
        b.append(tcap[kk]); k += 1
    A = sparse.coo_matrix((vals, (rows, cols)), shape=(k, 2 * nv)).tocsr()
    res = linprog(c, A_ub=A, b_ub=np.array(b), bounds=(0, None), method="highs")
    if res.status != 0:
        raise RuntimeError(f"LP failed: {res.message}")
    return res.x[:nv].reshape(D, R)


def metrics(x, need, w, p):
    unmet = np.maximum(need - x, 0.0)
    cost_unmet = (unmet * COST).sum(1)
    cost_need = (need * COST).sum(1)
    cov = np.where(cost_need > 0, 1 - cost_unmet / np.maximum(cost_need, 1e-12), 1.0)
    has = cost_need > 0
    srt = np.sort(cov[has]) if has.any() else np.array([1.0])
    n = len(srt)
    gini = float((2 * np.arange(1, n + 1) - n - 1).dot(srt) / (n * srt.sum())) if srt.sum() > 0 else 0.0
    return dict(weighted_unmet=float((w[:, None] * unmet * COST).sum()), unmet_cost=float(cost_unmet.sum()),
                need_cost=float(cost_need.sum()), min_coverage=float(cov[has].min()) if has.any() else 1.0,
                gini_coverage=gini, waste_cost=float((np.maximum(x - need, 0) * COST).sum()),
                spend=float((x * COST).sum()))


def baseline_population(need, p, budget):
    """Population-proportional split; budget divided across resources by national need-cost share."""
    share = (need * COST).sum(0)
    share = share / share.sum() if share.sum() > 0 else np.ones(len(COST)) / len(COST)
    units = budget * share / COST
    return p[:, None] * units[None, :]


def run() -> dict:
    dem = pd.read_csv(config.OUTPUTS / "demand.csv")
    dist = pd.read_csv(config.REFERENCE / "districts.csv").sort_values("district_id").reset_index(drop=True)
    reg = pd.read_csv(config.REFERENCE / "facilities.csv")
    ids = dist["district_id"].tolist()
    need = np.zeros((len(ids), len(RESOURCES)))
    stock = np.zeros_like(need); upper = np.zeros_like(need)
    for r in dem.itertuples():
        i, j = ids.index(r.district_id), RESOURCES.index(r.resource)
        need[i, j] = r.need_upper; stock[i, j] = r.stock; upper[i, j] = r.demand_upper
    pop = dist["population"].to_numpy(float)
    p = pop / pop.sum()
    beds = reg.groupby("district_id")["beds"].sum().reindex(ids).to_numpy(float)
    bp = beds / pop * 100000
    w = 1.0 + (1.0 - bp / bp.max())                       # vulnerability weight in [1, 2]
    divs = dist["division"].tolist()
    div_idx = np.array([sorted(set(divs)).index(x) for x in divs])
    total_need = (need * COST).sum()
    vol_need = (need * VOL).sum()
    tcap = {k: ASSUME["transport_tightness"] * p[div_idx == k].sum() * vol_need for k in np.unique(div_idx)}
    rows, alloc_rows = [], []
    for bf in ASSUME["budget_fractions"]:
        B = bf * total_need
        base_x = baseline_population(need, p, B)
        bm = metrics(base_x, need, w, p)
        for a in ASSUME["equity_floor_alphas"]:
            x = solve(need, w, p, div_idx, B, a, tcap)
            m = metrics(x, need, w, p)
            sid = f"b{int(bf * 100):03d}_a{int(a * 100):03d}"
            rows.append(dict(scenario_id=sid, budget_fraction=bf, alpha=a, budget=B, **m,
                             baseline_weighted_unmet=bm["weighted_unmet"], baseline_min_coverage=bm["min_coverage"],
                             baseline_gini=bm["gini_coverage"],
                             improvement_vs_baseline=1 - m["weighted_unmet"] / bm["weighted_unmet"]
                             if bm["weighted_unmet"] > 0 else 0.0))
            for i, d in enumerate(ids):
                for j, r in enumerate(RESOURCES):
                    alloc_rows.append((sid, d, r, need[i, j], stock[i, j], x[i, j], base_x[i, j]))
    sc = pd.DataFrame(rows)
    base0 = sc[sc["alpha"] == 0.0].set_index("budget_fraction")["weighted_unmet"]
    sc["efficiency_loss_vs_alpha0"] = [r.weighted_unmet / base0[r.budget_fraction] - 1 if base0[r.budget_fraction] > 0 else 0.0
                                       for r in sc.itertuples()]
    rec = {}
    for bf, g in sc.groupby("budget_fraction"):
        ok = g[g["efficiency_loss_vs_alpha0"] <= ASSUME["max_efficiency_loss_for_recommendation"] + 1e-9]
        best = ok.sort_values("alpha").iloc[-1]
        rec[str(bf)] = dict(alpha=float(best["alpha"]), scenario_id=best["scenario_id"],
                            efficiency_loss=float(best["efficiency_loss_vs_alpha0"]),
                            min_coverage=float(best["min_coverage"]))
    sc.to_csv(config.OUTPUTS / "allocation_scenarios.csv", index=False)
    pd.DataFrame(alloc_rows, columns=["scenario_id", "district_id", "resource", "need", "stock", "allocation",
                                      "baseline_allocation"]).to_csv(config.OUTPUTS / "allocation_grid.csv", index=False)
    pd.DataFrame(dict(district_id=ids, severity_weight=w, population_share=p, beds_per_100k=bp)).to_csv(
        config.OUTPUTS / "district_weights.csv", index=False)
    (config.OUTPUTS / "allocation_recommendation.json").write_text(json.dumps(
        dict(rule=f"largest alpha with weighted unmet demand within {ASSUME['max_efficiency_loss_for_recommendation']:.0%} of alpha=0",
             per_budget=rec), indent=1))
    return dict(scenarios=len(sc), recommended=rec)


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
