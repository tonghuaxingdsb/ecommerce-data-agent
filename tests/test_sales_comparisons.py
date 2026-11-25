from __future__ import annotations

import json
from typing import Any

import pytest

from ecommerce_agent.agent import build_agent_graph
from ecommerce_agent.answering import build_chinese_analysis
from ecommerce_agent.llm import LLMResult
from ecommerce_agent.sql_tool import SQLExecutionResult
from ecommerce_agent.text_to_sql import fallback_sql_plan
from ecommerce_agent.time_intelligence import build_sales_overview_sql


@pytest.mark.parametrize(
    ("question", "previous_gmv", "change_pct", "expected"),
    [
        ("近30天销售额同比去年如何？", None, None, "缺少去年同期的销售额数据，无法计算同比"),
        ("近30天销售额同比去年如何？", 0, None, "去年同期销售额为 0.00 元。比较基数为零，无法计算同比"),
        ("本月销售额环比上月如何？", 80, -25, "销售额环比下降 25.00%"),
        ("近30天销售额同比去年如何？", 50, 20, "销售额同比增长 20.00%"),
        ("本月销售额环比上月如何？", 60, 0, "销售额环比持平"),
    ],
)
def test_sales_comparison_summary_reports_available_baseline_and_direction(question, previous_gmv, change_pct, expected):
    answer = build_chinese_analysis(
        question,
        "sales_overview",
        [{"current_gmv": 60, "previous_gmv": previous_gmv, "change_pct": change_pct, "current_orders": 4}],
    )

    assert expected in answer
    assert "60.00 元" in answer
    assert "本期订单量 4 单" in answer
    assert "更强" not in answer
    if change_pct is None:
        assert "%" not in answer


def test_inherited_comparison_retains_original_period():
    previous = fallback_sql_plan("近7天销售额同比去年如何？", "sales_overview")
    plan = fallback_sql_plan("华东呢？", "sales_overview", inherited_query_spec=previous.query_spec)

    assert "r.area = '华东'" in plan.candidate_sql
    assert "INTERVAL '6 days'" in plan.candidate_sql
    assert "INTERVAL '365 days'" in plan.candidate_sql
    answer = build_chinese_analysis(
        "华东呢？", "sales_overview", [{"current_gmv": 60, "previous_gmv": None}], plan.query_spec
    )
    assert "近 7 天销售额" in answer
    assert "无法计算同比" in answer


def test_campaign_comparison_uses_campaign_bounds():
    sql, _ = build_sales_overview_sql("2025年双十一销售额同比如何？")

    assert "make_date(2025, 11, 11)" in sql
    assert "INTERVAL '1 year'" in sql
    assert "SUM(o.amount) FILTER" in sql
    assert "NULLIF(previous_gmv, 0)" in sql


def test_agent_grounds_sales_comparison_even_with_configured_model():
    class Gateway:
        is_configured = True

        def complete(self, **kwargs: Any) -> LLMResult:
            return LLMResult(
                text=json.dumps({"candidate_sql": "SELECT 0 AS current_gmv, 0 AS previous_gmv"}),
                configured=True,
            )

        def summarize(self, **kwargs: Any) -> LLMResult:
            raise AssertionError("Sales comparisons use a deterministic summary")

    def run_sql(sql: str) -> SQLExecutionResult:
        assert "previous_gmv" in sql
        assert "SUM(o.amount) FILTER" in sql
        first = {"current_gmv": 60, "previous_gmv": None, "change_pct": None, "current_orders": 4}
        return SQLExecutionResult(
            ok=True, sql=sql, executable_sql=sql, columns=list(first), rows=[first], row_count=1, elapsed_ms=1
        )

    result = build_agent_graph(Gateway(), sql_runner=run_sql).invoke({"question": "近30天销售额同比去年如何？"})

    assert "无法计算同比" in result["answer"]
    assert "增长 0.00%" not in result["answer"]
