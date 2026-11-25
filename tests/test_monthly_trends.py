from __future__ import annotations

import json
from functools import partial

import psycopg
import pytest

from ecommerce_agent.agent import build_agent_graph
from ecommerce_agent.config import Settings
from ecommerce_agent.llm import RuleBasedGateway
from ecommerce_agent.sql_tool import execute_sql
from ecommerce_agent.text_to_sql import parse_sql_plan


DATABASE_URL = "postgresql://agent:agent@localhost:5432/ecommerce_agent"
MONTH_EXPRESSION = "date_trunc('month', o.order_date)::date"


@pytest.mark.parametrize(
    ("question", "time_range"),
    [("今年各月的销售额趋势", "今年"), ("各月的销售额趋势", None)],
)
def test_monthly_request_overrides_daily_model_sql_and_chart(question, time_range):
    payload = {
        "query_spec": {
            "metrics": ["gmv"],
            "dimensions": ["order_date"],
            "time_range": "近 30 天",
            "limit": 1,
        },
        "candidate_sql": "SELECT o.order_date, SUM(o.amount) AS gmv FROM fact_orders o GROUP BY o.order_date",
        "chart_type": "line",
        "chart_x": "order_date",
        "chart_y": "gmv",
    }
    plan = parse_sql_plan(json.dumps(payload), "sales_overview", question)

    assert plan.query_spec["dimensions"] == ("order_month",)
    assert plan.query_spec["time_range"] == time_range
    assert f"{MONTH_EXPRESSION} AS order_month" in plan.candidate_sql
    assert f"GROUP BY {MONTH_EXPRESSION}" in plan.candidate_sql
    assert "ORDER BY order_month ASC" in plan.candidate_sql
    assert "LIMIT" not in plan.candidate_sql
    if time_range is None:
        assert "WITH bounds" not in plan.candidate_sql
    else:
        assert "o.order_date >= date_trunc('year', end_date)::date" in plan.candidate_sql
    assert (plan.chart_type, plan.chart_x, plan.chart_y) == ("line", "order_month", "gmv")


@pytest.mark.parametrize("question", ["今年各月的销售额趋势", "各月的销售额趋势"])
def test_mock_agent_returns_monthly_totals_and_peak_when_postgres_is_available(question):
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1) as connection:
            year_filter = ""
            if question.startswith("今年"):
                year_filter = "AND order_date >= date_trunc('year', (SELECT MAX(order_date) FROM fact_orders))"
            expected = connection.execute(
                "SELECT date_trunc('month', order_date)::date, SUM(amount) "
                "FROM fact_orders WHERE status = 'paid' "
                f"{year_filter} GROUP BY 1 ORDER BY 1"
            ).fetchall()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available.")

    settings = Settings(database_url=DATABASE_URL)
    graph = build_agent_graph(RuleBasedGateway(settings), sql_runner=partial(execute_sql, settings=settings))
    result = graph.invoke({"question": question, "trace": []})

    assert result["columns"] == ["order_month", "gmv"]
    assert result["rows"] == [{"order_month": month.isoformat(), "gmv": float(gmv)} for month, gmv in expected]
    peak_month = max(expected, key=lambda row: row[1])[0].isoformat()[:7]
    assert f"峰值出现在 {peak_month}，当月" in result["answer"]
    assert result["chart"]["type"] == "line"
    assert result["chart"]["x"] == "order_month"
