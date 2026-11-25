from __future__ import annotations

import json
from functools import partial

import psycopg
import pytest

from ecommerce_agent.agent import build_agent_graph
from ecommerce_agent.config import Settings
from ecommerce_agent.llm import RuleBasedGateway
from ecommerce_agent.sql_tool import execute_sql
from ecommerce_agent.text_to_sql import fallback_sql_plan, parse_sql_plan


DATABASE_URL = "postgresql://agent:agent@localhost:5432/ecommerce_agent"


@pytest.mark.parametrize("question", ["双十一期间的销售额是多少？", "双11销售额是多少？"])
def test_named_campaign_overrides_unbounded_model_sql(question):
    plan = parse_sql_plan(json.dumps({"candidate_sql": "SELECT SUM(amount) FROM fact_orders"}), "sales_overview", question)
    assert plan.query_spec["time_range"] == "双十一"
    assert "o.order_date = (CASE WHEN make_date(" in plan.candidate_sql
    assert ", 11, 11)" in plan.candidate_sql


def test_ambiguous_campaign_requires_a_named_event():
    plan = fallback_sql_plan("大促期间的销售额是多少？", "sales_overview")
    assert plan.needs_clarification
    assert "双十一" in plan.clarifying_question
    assert not plan.candidate_sql


@pytest.mark.parametrize(
    ("question", "reference_sql", "columns"),
    [
        ("2026年双十一期间的销售额是多少？", "SELECT SUM(amount) FROM fact_orders WHERE status = 'paid' AND order_date = DATE '2026-11-11' HAVING EXISTS (SELECT 1 FROM fact_orders WHERE order_date = DATE '2026-11-11')", ["gmv"]),
        ("哪个商品卖得最差？", "SELECT p.product_name, SUM(o.quantity) AS units FROM fact_orders o JOIN dim_product p USING (product_id) WHERE o.status = 'paid' GROUP BY 1 ORDER BY units LIMIT 1", ["product_name", "units"]),
        ("退款订单有多少？", "SELECT COUNT(DISTINCT order_id) FROM fact_orders WHERE status = 'refunded'", ["refunded_orders"]),
        ("会员等级分布如何？", "SELECT vip_level, COUNT(DISTINCT user_id) FROM dim_user GROUP BY 1 ORDER BY 2 DESC", ["vip_level", "users"]),
        ("本月新增用户有多少？", "WITH bounds AS (SELECT date_trunc('month', MAX(order_date)) AS month FROM fact_orders) SELECT COUNT(DISTINCT user_id) FROM dim_user CROSS JOIN bounds WHERE register_date >= month AND register_date < month + INTERVAL '1 month'", ["registered_users"]),
    ],
)
def test_mock_agent_matches_database_for_campaign_and_common_metrics(question, reference_sql, columns):
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1) as connection:
            expected = connection.execute(reference_sql).fetchall()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available.")

    settings = Settings(database_url=DATABASE_URL)
    graph = build_agent_graph(RuleBasedGateway(settings), sql_runner=partial(execute_sql, settings=settings))
    result = graph.invoke({"question": question, "trace": []})

    assert result["columns"] == columns
    actual = [tuple(row[column] for column in columns) for row in result["rows"]]
    if columns == ["gmv"] and expected:
        assert actual == [(float(expected[0][0]),)]
    elif columns == ["product_name", "units"] and expected:
        assert len(actual) == 1 and actual[0][1] == expected[0][1]
        assert "最低" in result["answer"]
    else:
        assert set(actual) == set(expected)


def test_campaign_outside_database_history_has_no_rows():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1):
            pass
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available.")

    settings = Settings(database_url=DATABASE_URL)
    graph = build_agent_graph(RuleBasedGateway(settings), sql_runner=partial(execute_sql, settings=settings))
    result = graph.invoke({"question": "2000年618销量是多少？", "trace": []})

    assert result["columns"] == ["units"]
    assert result["rows"] == []
    assert "2000年618" in result["answer"]
