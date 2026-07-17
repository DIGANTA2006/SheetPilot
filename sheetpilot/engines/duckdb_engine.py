"""Trusted-code-only DuckDB adapter for large CSV unions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import duckdb
import polars as pl

from sheetpilot.security.path_guard import ensure_within


class DuckDBEngine:
    """Execute fixed application queries; plans can never supply SQL text."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def union_csv_files(self, paths: list[Path], *, union_by_name: bool = True) -> pl.DataFrame:
        guarded = [str(ensure_within(path, self.workspace_root)) for path in paths]
        connection = duckdb.connect(":memory:")
        try:
            relation = cast(Any, connection).read_csv(guarded, union_by_name=union_by_name)
            return cast(pl.DataFrame, relation.pl())
        finally:
            connection.close()
