from __future__ import annotations

from datetime import date

import psycopg
import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.answering import build_fallback_analysis, is_usable_analysis
from ecommerce_agent.semantic import build_query_spec, validate_sql_semantics
from ecommerce_agent.sql_tool import execute_sql
from ecommerce_agent.text_to_sql import fallback_sql_plan, parse_sql_plan


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
    assert "ORDER BY order_date ASC" in plan.candidate_sql


def test_inventory_planning_uses_registered_forecast_capability():
    question = "按照趋势下个月各种商品应该准备多少库存量"

    plan = fallback_sql_plan(question, "inventory_planning")

    assert plan.intent == "inventory_planning"
    assert plan.needs_clarification is False
    assert plan.query_spec["task_type"] == "inventory_planning"
    assert plan.query_spec["capability"] == "inventory_forecast"
    assert plan.query_spec["metrics"] == ("units",)
    assert plan.query_spec["dimensions"] == ("product",)
    assert plan.query_spec["training_window_days"] == 90
    assert plan.query_spec["forecast_horizon"] == "next_calendar_month"
    assert plan.query_spec["safety_stock_days"] == 7
    assert "regr_slope" in plan.candidate_sql
    assert "forecast_units" in plan.candidate_sql
    assert "current_stock" in plan.candidate_sql
    assert "recommended_restock" in plan.candidate_sql
    assert "WHERE recommended_restock > 0" in plan.candidate_sql
    assert "ORDER BY recommended_restock DESC" in plan.candidate_sql
    assert plan.chart_y == "recommended_restock"
    assert validate_sql_semantics(build_query_spec(question), plan.candidate_sql).ok is True


def test_unregistered_forecast_does_not_fall_back_to_gmv():
    plan = fallback_sql_plan("预测下个月销售额", "forecasting")

    assert plan.intent == "forecasting"
    assert plan.needs_clarification is True
    assert plan.candidate_sql == ""
    assert plan.query_spec["capability"] == "unsupported_forecast"
    assert "当前仅注册" in plan.clarifying_question


def test_inventory_planning_compiles_next_calendar_quarter():
    question = "下季度各商品应该准备多少库存"

    plan = fallback_sql_plan(question, "inventory_planning")

    assert plan.needs_clarification is False
    assert plan.query_spec["capability"] == "inventory_forecast"
    assert plan.query_spec["forecast_horizon"] == "next_calendar_quarter"
    assert "date_trunc('quarter', max(order_date))" in plan.candidate_sql
    assert "INTERVAL '3 months'" in plan.candidate_sql
    assert "INTERVAL '6 months'" in plan.candidate_sql
    assert "下个自然季度" in plan.summary


def test_inventory_safety_stock_uses_exact_integer_ceiling_when_postgres_is_available():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available.")

    plan = fallback_sql_plan("下周各商品应该补货多少", "inventory_planning")
    result = execute_sql(plan.candidate_sql, settings=Settings(database_url=DATABASE_URL))

    assert result.ok is True
    assert result.rows
    for row in result.rows:
        days = (date.fromisoformat(row["forecast_end"]) - date.fromisoformat(row["forecast_start"])).days + 1
        exact_ceiling = (row["forecast_units"] * 7 + days - 1) // days
        assert row["safety_stock_units"] == exact_ceiling


def test_product_contribution_follow_up_inherits_metric_time_and_comparison():
    # Use the exact tuple-based representation stored by ConversationTurn.
    inherited = build_query_spec("近 30 天销售额多少？环比上个月呢？").to_dict()

    plan = fallback_sql_plan(
        "主要贡献来源是什么商品",
        "product_analysis",
        inherited_query_spec=inherited,
    )

    assert plan.needs_clarification is False
    assert plan.query_spec["metrics"] == ("gmv",)
    assert plan.query_spec["dimensions"] == ("product",)
    assert plan.query_spec["time_range"] == "近 30 天"
    assert plan.query_spec["analysis_type"] == "contribution"
    assert plan.query_spec["comparison"] == "mom"
    assert "current_gmv" in plan.candidate_sql
    assert "previous_gmv" in plan.candidate_sql
    assert "gmv_change" in plan.candidate_sql
    assert "ORDER BY gmv_change DESC" in plan.candidate_sql


def test_grounded_follow_up_overrides_spurious_model_clarification():
    model_payload = """{
      "intent": "product_analysis",
      "needs_clarification": true,
      "clarifying_question": "请明确指标",
      "query_spec": {"dimensions": ["product"]},
      "candidate_sql": ""
    }"""

    plan = parse_sql_plan(
        model_payload,
        "product_analysis",
        "主要贡献来源是什么商品",
        inherited_query_spec={"metrics": ["gmv"], "time_range": "近 30 天", "comparison": "mom"},
    )

    assert plan.needs_clarification is False
    assert plan.clarifying_question == ""
    assert "ORDER BY gmv_change DESC" in plan.candidate_sql


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


@pytest.mark.parametrize("text", ["下月（", "这是一段长度足够但括号没有闭合的分析（", "主要商品包括"])
def test_analysis_quality_gate_rejects_incomplete_text(text):
    assert is_usable_analysis(text, "zh") is False


def test_analysis_quality_gate_accepts_complete_text():
    assert is_usable_analysis("下月预计需要补货 179 件，建议优先复核头部商品的在途库存。", "zh") is True


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


def test_product_gmv_contribution_follow_up_executes_when_postgres_is_available():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available.")

    plan = fallback_sql_plan(
        "主要贡献来源是什么商品",
        "product_analysis",
        inherited_query_spec={"metrics": ["gmv"], "time_range": "近 30 天", "comparison": "mom"},
    )
    result = execute_sql(
        plan.candidate_sql,
        settings=Settings(database_url=DATABASE_URL, sql_max_rows=20, sql_timeout_ms=3000),
    )

    assert result.ok is True
    assert result.columns == ["product_name", "current_gmv", "previous_gmv", "gmv_change", "change_pct", "contribution_pct"]
    assert result.rows[0]["gmv_change"] >= result.rows[1]["gmv_change"]


def test_inventory_forecast_executes_when_postgres_is_available():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT (date_trunc('quarter', max(order_date)) + INTERVAL '3 months')::date,
                           (date_trunc('quarter', max(order_date)) + INTERVAL '6 months - 1 day')::date
                    FROM fact_orders
                """)
                expected_start, expected_end = cursor.fetchone()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available.")

    plan = fallback_sql_plan("下季度各商品应该准备多少库存", "inventory_planning")
    result = execute_sql(
        plan.candidate_sql,
        settings=Settings(database_url=DATABASE_URL, sql_max_rows=200, sql_timeout_ms=5000),
    )

    assert result.ok is True, result.error
    assert result.columns == [
        "product_name",
        "forecast_start",
        "forecast_end",
        "current_stock",
        "avg_daily_units",
        "trend_units_per_day",
        "forecast_units",
        "safety_stock_units",
        "target_stock",
        "recommended_restock",
    ]
    assert result.row_count > 0
    assert result.rows[0]["forecast_start"] == expected_start.isoformat()
    assert result.rows[0]["forecast_end"] == expected_end.isoformat()
    assert result.rows[0]["recommended_restock"] >= result.rows[-1]["recommended_restock"]
