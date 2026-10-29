"""Small helpers for reading lake tables into pandas (outputs are small aggregates only)."""
import pandas as pd
from common import config


def read_table(layer: str, name: str, **kw) -> pd.DataFrame:
    df = pd.read_parquet(config.LAKE / layer / name, **kw)
    for c in df.columns:                         # partition columns arrive as categoricals
        if isinstance(df[c].dtype, pd.CategoricalDtype):
            cats = df[c].astype(str)
            df[c] = pd.to_numeric(cats) if cats.str.fullmatch(r"-?\d+").all() else cats
    return df
