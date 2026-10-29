"""Epidemiological week helpers.

Definition used throughout EpiScope (documented in the report): weeks start on Sunday (Bangladesh
working week). A week belongs to the calendar year of its Wednesday; epi_week = ceil(day_of_year(Wednesday) / 7).
"""
import datetime as dt
import math
from pyspark.sql import functions as F


def week_start_col(date_col):
    """Sunday on or before the date. Spark dayofweek: Sunday=1."""
    return F.date_sub(date_col, F.dayofweek(date_col) - 1)


def epi_year_col(week_start_col_):
    return F.year(F.date_add(week_start_col_, 3))


def epi_week_col(week_start_col_):
    return F.ceil(F.dayofyear(F.date_add(week_start_col_, 3)) / 7).cast("int")


def week_start(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)     # Monday=0 ... Sunday=6


def epi_year_week(ws: dt.date):
    wed = ws + dt.timedelta(days=3)
    return wed.year, math.ceil(wed.timetuple().tm_yday / 7)


def epi_week_start(year: int, week: int) -> dt.date:
    """Sunday starting epi week `week` of `year` (inverse of epi_year_week)."""
    jan1 = dt.date(year, 1, 1)
    first_wed = jan1 + dt.timedelta(days=(2 - jan1.weekday()) % 7)      # Wednesday has weekday 2
    return first_wed + dt.timedelta(days=7 * (week - 1)) - dt.timedelta(days=3)
