"""PRV-2: no count 1..4 anywhere and no recovery of a suppressed cell by subtraction."""
import numpy as np
from serving.suppress import suppress_group, recoverable, suppress_value, is_small


def test_primary_suppression():
    assert suppress_value(0) == 0 and suppress_value(5) == 5
    assert all(suppress_value(v) is None for v in (1, 2, 3, 4))


def test_group_properties_random():
    rng = np.random.default_rng(1)
    for _ in range(5000):
        n = int(rng.integers(2, 14))
        cells = list(rng.choice([0, 0, 1, 2, 3, 4, 5, 6, 9, 15, 40], n))
        shown, total = suppress_group(cells)
        assert all(s is None or not is_small(s) for s in shown)
        assert total is None or not is_small(total)
        assert not recoverable(shown, total), (cells, shown, total)
        for c, s in zip(cells, shown):                      # visible cells are exact
            assert s is None or s == c


def test_single_small_cell_triggers_complementary():
    shown, total = suppress_group([3, 7, 10, 0])
    assert sum(s is None for s in shown) == 2 and total == 20
