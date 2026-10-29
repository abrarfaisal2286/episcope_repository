"""AN-7: optimiser constraints (budget, equity floor, non-negativity, transport) and optimality checks."""
import numpy as np
from analytics.optimise import allocate as A


def instance(seed=0, D=12):
    rng = np.random.default_rng(seed)
    R = len(A.COST)
    need = rng.gamma(2.0, 200.0, (D, R)) * (rng.random((D, R)) < 0.8)
    p = rng.dirichlet(np.ones(D)); w = 1 + rng.random(D)
    div = np.repeat(np.arange(3), D // 3)
    tcap = {k: 1.2 * p[div == k].sum() * (need * A.VOL).sum() for k in range(3)}
    return need, w, p, div, tcap


def test_constraints_hold():
    need, w, p, div, tcap = instance()
    B = 0.5 * (need * A.COST).sum()
    for alpha in (0.0, 0.3, 0.6):
        x = A.solve(need, w, p, div, B, alpha, tcap)
        assert (x >= -1e-7).all()
        assert (x * A.COST).sum() <= B * (1 + 1e-6)
        tot = x.sum(0)
        assert (x >= alpha * p[:, None] * tot[None, :] - 1e-5).all()
        for k, cap in tcap.items():
            assert (x[div == k] * A.VOL).sum() <= cap * (1 + 1e-6)


def test_optimiser_beats_population_baseline_and_equity_costs_efficiency():
    need, w, p, div, tcap = instance(3)
    B = 0.4 * (need * A.COST).sum()
    base = A.metrics(A.baseline_population(need, p, B), need, w, p)["weighted_unmet"]
    u0 = A.metrics(A.solve(need, w, p, div, B, 0.0, tcap), need, w, p)["weighted_unmet"]
    u5 = A.metrics(A.solve(need, w, p, div, B, 0.5, tcap), need, w, p)["weighted_unmet"]
    assert u0 <= base + 1e-6
    assert u5 >= u0 - 1e-6                       # a stricter equity floor cannot reduce weighted unmet demand


def test_full_budget_covers_need_when_transport_allows():
    need, w, p, div, tcap = instance(5)
    tcap = {k: 1e12 for k in tcap}
    x = A.solve(need, w, p, div, 2.0 * (need * A.COST).sum(), 0.0, tcap)
    assert A.metrics(x, need, w, p)["weighted_unmet"] < 1e-4
