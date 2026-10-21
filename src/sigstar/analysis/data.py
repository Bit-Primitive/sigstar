"""Read the built marts into pandas for analysis (read-only connection)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import duckdb
import pandas as pd

from sigstar.config import Config
from sigstar.errors import SigstarError


@contextmanager
def connect(cfg: Config) -> Iterator[duckdb.DuckDBPyConnection]:
    if not cfg.warehouse.exists():
        raise SigstarError(
            f"the warehouse {cfg.warehouse} does not exist yet",
            "run `sigstar collect` and `sigstar build` first (or `sigstar run`)",
        )
    con = duckdb.connect(str(cfg.warehouse), read_only=True)
    try:
        con.execute("set TimeZone = 'UTC'")
        yield con
    finally:
        con.close()


def _qualified(con: duckdb.DuckDBPyConnection, table: str) -> str:
    # Qualify with the catalog: a warehouse file named sigstar.duckdb makes a bare
    # "sigstar.<table>" ambiguous between the catalog and the schema.
    catalog = con.execute("select current_database()").fetchone()[0]
    return f'"{catalog}".sigstar.{table}'


def read_table(
    cfg: Config, table: str, where: str = "", params: list | None = None
) -> pd.DataFrame:
    with connect(cfg) as con:
        try:
            sql = f"select * from {_qualified(con, table)} {where}"
            return con.execute(sql, params or []).df()
        except duckdb.CatalogException as exc:
            raise SigstarError(
                f"table sigstar.{table} is missing from {cfg.warehouse}",
                "run `sigstar build` to create the marts",
            ) from exc


def features(cfg: Config) -> pd.DataFrame:
    df = read_table(cfg, "features_daily", "order by asset, day")
    if df.empty:
        raise SigstarError(
            "features_daily is empty: no daily prices have been collected yet",
            "run `sigstar collect` then `sigstar build`",
        )
    df["day"] = pd.to_datetime(df["day"]).dt.date
    return df


def headlines(cfg: Config, tag: str, day: date, limit: int = 5) -> pd.DataFrame:
    """Headlines tagged with an asset or topic on one UTC day, feed items first (they carry
    a publisher snippet), then GDELT's list in its relevance order."""
    with connect(cfg) as con:
        news = _qualified(con, "fct_news")
        bridge = _qualified(con, "bridge_news_tags")
        return con.execute(
            f"""
            select n.title, n.url, coalesce(n.source_name, n.domain) as source, n.provider,
                n.published_at, b.matched_keyword
            from {news} as n
            join {bridge} as b using (article_id)
            where b.tag = ? and n.day = ?
            order by case when n.provider = 'rss' then 0 else 1 end, n.published_at
            limit ?
            """,
            [tag, day, limit],
        ).df()
