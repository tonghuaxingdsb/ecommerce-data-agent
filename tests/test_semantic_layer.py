from __future__ import annotations

import psycopg
import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.answering import build_fallback_analysis
from ecommerce_agent.semantic import build_query_spec, validate_sql_semantics
from ecommerce_agent.sql_tool import execute_sql
from ecommerce_agent.text_to_sql import fallback_sql_plan


DATABASE_URL = "postgresql://agent:agent@localhost:5432/ecommerce_agent"


@pytest.mark.parametrize(
    ("question", "metric", "dimension", "order_fragment"),
    [
        ("销量最高的商品", "units", "product", "ORDER BY units DESC"),
        ("哪个类目销量最高", "units", "category", "ORDER BY units DESC"),
        ("哪个渠道订单量最高", "orders", "channel", "ORDER BY orders DESC"),
        ("哪个地区销售额最高", "gmv", "area", "ORDER BY gmv DESC"),
    ],
)
def test_query_spec_composes_metric_dimension_and_ranking(question, metric, dimension, order_fragment):
    plan = fallback_sql_plan(question, "general_analysis")

    assert plan.query_spec["metrics"] == (metric,)
    assert plan.query_spec["dimensions"] == (dimension,)
    assert order_fragment in plan.candidate_sql
    assert validate_sql_semantics(build_query_spec(question, plan.query_spec), plan.candidate_sql).ok is True


def test_product_repurchase_is_not_rewritten_as_gmv():
    question = "什么商品的复购率高"
    plan = fallback_sql_plan(question, "product_analysis")

    assert plan.query_spec["metrics"] == ("repurchase_rate",)
    assert plan.query_spec["dimensions"] == ("product",)
    assert "paid_orders >= 2" in plan.candidate_sql
    assert "repurchase_rate_pct" in plan.candidate_sql
    assert "ORDER BY repurchase_rate_pct DESC" in plan.candidate_sql
    assert "ORDER BY gmv" not in plan.candidate_sql


def test_semantic_validator_rejects_gmv_sql_for_repurchase_question():
    spec = build_query_spec("什么商品的复购率高")
    wrong_sql = """
SELECT p.product_name, SUM(o.amount) AS gmv
FROM fact_orders o JOIN dim_product p ON p.product_id = o.product_id
WHERE o.status = 'paid'
GROUP BY p.product_name
ORDER BY gmv DESC
""".strip()

    result = validate_sql_semantics(spec, wrong_sql)

    assert result.ok is False
    assert "复购率" in result.message
    assert "repurchase_rate_pct" in result.message


def test_time_and_area_constraints_are_preserved_in_generic_query():
    question = "近30天华东销量最高的商品"
    plan = fallback_sql_plan(question, "product_analysis")

    assert "CROSS JOIN bounds" in plan.candidate_sql
    assert "o.order_date BETWEEN end_date - INTERVAL '29 days' AND end_date" in plan.candidate_sql
    assert "JOIN dim_region r" in plan.candidate_sql
    assert "r.area = '华东'" in plan.candidate_sql


def test_unsupported_conversion_rate_requests_clarification():
    plan = fallback_sql_plan("哪个渠道转化率最高", "channel_analysis")

    assert plan.needs_clarification is True
    assert plan.candidate_sql == ""
    assert "曝光" in plan.clarifying_question


def test_sales_trend_defaults_to_gmv_and_keeps_recent_time_range():
    question = "最近30天每日销售趋势"
    plan = fallback_sql_plan(question, "sales_overview")

    assert plan.query_spec["metrics"] == ("gmv",)
    assert plan.query_spec["dimensions"] == ("order_date",)
    assert plan.query_spec["time_range"] == "近 30 天"
    assert "GROUP BY o.order_date" in plan.candidate_sql


def test_fallback_summary_reads_generic_result_columns_without_zero_placeholders():
    sales_answer = build_fallback_analysis("近30天销售额多少", "sales_overview", [{"gmv": 12345.0}], "zh")
    product_answer = build_fallback_analysis(
        "Top10商品是哪些",
        "product_analysis",
        [{"product_name": "测试商品", "gmv": 999.0}],
        "zh",
    )

    assert "1.23 万元" in sales_answer
    assert "0.00" not in sales_answer
    assert "测试商品" in product_answer
    assert "None" not in product_answer


def test_product_repurchase_query_executes_when_postgres_is_available():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available.")

    plan = fallback_sql_plan("什么商品的复购率高", "product_analysis")
    result = execute_sql(
        plan.candidate_sql,
        settings=Settings(database_url=DATABASE_URL, sql_max_rows=20, sql_timeout_ms=3000),
    )

    assert result.ok is True
    assert result.columns == ["product_name", "paid_users", "repeat_users", "repurchase_rate_pct"]
    assert result.rows[0]["repurchase_rate_pct"] >= result.rows[1]["repurchase_rate_pct"]
    assert result.rows[0]["paid_users"] >= 20
