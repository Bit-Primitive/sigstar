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


# Venue or provider values in the marts, and the source each comes from.
_PRICE_VENUES = {"coinbase": "coinbase", "kraken": "kraken", "binance": "binance"}
_DERIVATIVE_VENUES = {"binance": "binance_futures", "okx": "okx"}
SOURCE_ORDER = (
    "coinbase",
    "kraken",
    "binance",
    "binance_futures",
    "okx",
    "fear_greed",
    "gdelt",
    "rss",
    "fred",
    "chainstar",
)


def sources_used(cfg: Config) -> tuple[list[str], list[str]]:
    """The sources whose data is actually in the marts (after the licence mode's filters),
    in a fixed order, and the RSS feed names among them. This is what reports credit and
    state, rather than what sigstar.yml switches on."""
    found: set[str] = set()
    with connect(cfg) as con:

        def rows(table: str, sql: str) -> list[tuple]:
            try:
                return con.execute(sql.format(t=_qualified(con, table))).fetchall()
            except duckdb.CatalogException:
                return []

        for (venue,) in rows("fct_prices", "select distinct venue from {t}"):
            if venue in _PRICE_VENUES:
                found.add(_PRICE_VENUES[venue])
            elif venue == "onchain":
                found.add("chainstar")
        for (venue,) in rows("fct_derivatives", "select distinct venue from {t}"):
            if venue in _DERIVATIVE_VENUES:
                found.add(_DERIVATIVE_VENUES[venue])
        if rows("fct_sentiment", "select 1 from {t} limit 1"):
            found.add("fear_greed")
        if rows("agg_news_daily", "select 1 from {t} where gdelt_all_articles is not null limit 1"):
            found.add("gdelt")
        if rows("fct_news", "select 1 from {t} where provider = 'gdelt' limit 1"):
            found.add("gdelt")
        feeds = [
            str(name)
            for (name,) in rows(
                "fct_news",
                "select distinct source_name from {t} where provider = 'rss' order by 1",
            )
        ]
        if feeds:
            found.add("rss")
        if rows("fct_macro", "select 1 from {t} limit 1"):
            found.add("fred")
        if rows("fct_onchain_daily", "select 1 from {t} limit 1"):
            found.add("chainstar")
    return [s for s in SOURCE_ORDER if s in found], feeds


def onchain_parts(cfg: Config) -> pd.DataFrame:
    """Each on-chain feature's part per chain and day, with that chain's blocks loaded
    (dbt: int_onchain_feature_parts); empty when there is none."""
    columns = ["asset", "day", "feature", "chain", "value", "blocks"]
    with connect(cfg) as con:
        catalog = con.execute("select current_database()").fetchone()[0]
        try:
            df = con.execute(
                f'select {", ".join(columns)} from "{catalog}".sigstar_staging.'
                "int_onchain_feature_parts where value is not null"
            ).df()
        except duckdb.CatalogException:
            return pd.DataFrame(columns=columns)
    df["day"] = pd.to_datetime(df["day"]).dt.date
    return df


def headlines(cfg: Config, tag: str, day: date, limit: int = 5) -> pd.DataFrame:
    """Headlines tagged with an asset or topic on one UTC day, feed items first (they carry
    a publisher snippet), then GDELT's list in its relevance order. A story syndicated
    under the same title (the same article on aol.com and fool.com) is listed once."""
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
            qualify row_number() over (
                partition by lower(n.title)
                order by case when n.provider = 'rss' then 0 else 1 end, n.published_at
            ) = 1
            order by case when n.provider = 'rss' then 0 else 1 end, n.published_at
            limit ?
            """,
            [tag, day, limit],
        ).df()
