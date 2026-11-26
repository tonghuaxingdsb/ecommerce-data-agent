from __future__ import annotations

import json

import psycopg
import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.sql_tool import execute_sql
from ecommerce_agent.text_to_sql import fallback_sql_plan, parse_sql_plan


DATABASE_URL = "postgresql://agent:agent@localhost:5432/ecommerce_agent"


@pytest.mark.parametrize(
    ("question", "time_range", "dimension"),
    [
        ("GMV by channel last month", "上个月", "channel"),
        ("GMV by category for the last 7 days", "近 7 天", "category"),
        ("GMV by region yesterday", "昨天", "area"),
        ("GMV this quarter", "本季度", None),
        ("1999 年销售额", "1999年", None),
    ],
)
def test_explicit_time_scope_replaces_unbounded_model_sql(question, time_range, dimension):
    payload = {"candidate_sql": "SELECT SUM(o.amount) AS gmv FROM fact_orders o WHERE o.status = 'paid'"}
    plan = parse_sql_plan(json.dumps(payload), "sales_overview", question)
    assert not plan.needs_clarification
    assert plan.query_spec["time_range"] == time_range
    assert "o.order_date" in plan.candidate_sql
    assert bool(dimension) == ("GROUP BY" in plan.candidate_sql)
    if dimension:
        assert plan.query_spec["dimensions"] == (dimension,)


@pytest.mark.parametrize(
    "question",
    [
        "GMV by salesperson last month",
        "GMV by channel and salesperson last month",
        "GMV by channel next month",
        "GMV from last month to this month",
        "GMV by channel on Monday",
        "近 7 天或近 30 天销售额",
        "2026 年 5 月销售额",
    ],
)
def test_unsupported_explicit_scope_is_clarified(question):
    plan = fallback_sql_plan(question, "sales_overview")
    assert plan.needs_clarification
    assert not plan.candidate_sql


def test_last_month_channel_gmv_matches_postgres():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1) as connection:
            expected = connection.execute("""
                SELECT channel, SUM(amount) FROM fact_orders
                WHERE status = 'paid'
                  AND order_date >= date_trunc('month', (SELECT MAX(order_date) FROM fact_orders)) - INTERVAL '1 month'
                  AND order_date < date_trunc('month', (SELECT MAX(order_date) FROM fact_orders))
                GROUP BY channel
            """).fetchall()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available.")

    plan = fallback_sql_plan("GMV by channel last month", "sales_overview")
    result = execute_sql(plan.candidate_sql, settings=Settings(database_url=DATABASE_URL))
    assert result.ok, result.error
    assert {row["channel"]: row["gmv"] for row in result.rows} == {
        channel: float(gmv) for channel, gmv in expected
    }
