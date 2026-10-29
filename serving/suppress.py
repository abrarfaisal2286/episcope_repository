"""Small-cell suppression with complementary suppression (PRV-2).

Policy: any displayed count between 1 and 4 is suppressed (None). Because groups also ship a total,
a single suppressed cell could be recovered by subtraction; therefore when exactly one cell of a group is
suppressed while the total is shown, the smallest remaining cell with value >= 5 is suppressed as well.
If no such cell exists the total is suppressed instead. Zero is not a small cell and is shown.
"""
from common.config import SMALL_CELL_MAX


def is_small(v) -> bool:
    return v is not None and 1 <= v <= SMALL_CELL_MAX


def suppress_value(v):
    """Primary suppression of one count (also used for rounded model estimates)."""
    if v is None:
        return None
    return None if is_small(v) else v


def suppress_group(cells):
    """cells: list of true integer counts. Returns (displayed_cells, displayed_total)."""
    total = int(sum(cells))
    shown = [None if is_small(c) else int(c) for c in cells]
    total_shown = None if is_small(total) else total
    n_sup = sum(1 for s in shown if s is None)
    if total_shown is not None and n_sup == 1:
        cand = [(c, i) for i, c in enumerate(cells) if shown[i] is not None and c >= SMALL_CELL_MAX + 1]
        if cand:
            _, i = min(cand)
            shown[i] = None
        else:
            total_shown = None
    return shown, total_shown


def recoverable(shown, total_shown) -> bool:
    """True if a suppressed cell could be derived by subtraction (should never happen)."""
    n_sup = sum(1 for s in shown if s is None)
    return total_shown is not None and n_sup == 1
