"""Narrow pandas compatibility adapter; Polars remains primary."""

import pandas as pd
import polars as pl


def to_pandas(frame: pl.DataFrame) -> pd.DataFrame:
    """Convert only when a compatibility-only library requires pandas."""
    return pd.DataFrame(frame.to_dict(as_series=False))


def from_pandas(frame: pd.DataFrame) -> pl.DataFrame:
    """Return compatibility results to the primary Polars representation."""
    return pl.DataFrame(frame.to_dict(orient="list"), strict=False)
