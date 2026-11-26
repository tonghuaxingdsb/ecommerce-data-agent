from __future__ import annotations

from typing import Any

import pytest

from ecommerce_agent.agent import build_agent_graph, stream_agent_events
from ecommerce_agent.answering import build_fallback_analysis
from ecommerce_agent.config import Settings
from ecommerce_agent.conversation import ConversationTurn
from ecommerce_agent.llm import build_gateway
from ecommerce_agent.sql_tool import SQLExecutionResult
from ecommerce_agent.text_to_sql import fallback_sql_plan


def _result(sql: str, columns: list[str], rows: list[dict[str, Any]]) -> SQLExecutionResult:
    return SQLExecutionResult(
        ok=True, sql=sql, executable_sql=sql, columns=columns, rows=rows, row_count=len(rows), elapsed_ms=1
    )


def _repurchase_result(sql: str) -> SQLExecutionResult:
    row = {"product_name": "测试商品", "paid_users": 80, "repeat_users": 40, "repurchase_rate_pct": 50.0}
    return _result(sql, list(row), [row])


@pytest.mark.parametrize("provider", ["mock", "deepseek"])
def test_offline_ranking_followup_uses_current_metric(provider: str):
    def run_sql(sql: str) -> SQLExecutionResult:
        if "repurchase_rate_pct" in sql:
            return _repurchase_result(sql)
        assert "ORDER BY units DESC" in sql
        return _result(sql, ["product_name", "units"], [{"product_name": "测试商品", "units": 3920}])

    gateway = build_gateway(Settings(llm_provider=provider, deepseek_api_key=None))
    graph = build_agent_graph(gateway, sql_runner=run_sql)
    first = graph.invoke({"question": "什么商品的复购率高"})
    history = [ConversationTurn(question=first["question"], query_spec=first["query_spec"])]

    second = graph.invoke({"question": "那销量呢？", "history": history})

    assert second["query_spec"]["metrics"] == ("units",)
    assert second["query_spec"]["dimensions"] == ("product",)
    assert second["columns"] == ["product_name", "units"]
    assert "按销量看，商品「测试商品」排名最高" in second["answer"]
    assert "3,920" in second["answer"]
    assert all(text not in second["answer"] for text in ("None", "0.00 元", "0 单", "贡献", "复购率"))


def test_product_summary_uses_only_available_values_without_query_spec():
    answer = build_fallback_analysis(
        "查看商品数据",
        "product_analysis",
        [{"product_name": "测试商品", "category": None, "gmv": None, "units": 3920}],
        "zh",
    )

    assert "测试商品" in answer
    assert "销量 3,920 件" in answer
    assert all(text not in answer for text in ("None", "类目", "销售额", "订单量", "贡献"))


def test_offline_chart_title_only_describes_analysis_in_query_and_stream():
    question = "什么商品的复购率高"
    gateway = build_gateway(Settings(llm_provider="mock", deepseek_api_key=None))

    result = build_agent_graph(gateway, sql_runner=_repurchase_result).invoke({"question": question})
    stream_result = list(stream_agent_events({"question": question}, llm=gateway, sql_runner=_repurchase_result))[-1]
    title = fallback_sql_plan(question, "user_analysis").summary

    assert result["chart"]["title"] == title
    assert stream_result["result"]["chart"]["title"] == title
    assert "provider" not in title


@pytest.mark.parametrize(
    ("question", "language", "expected"),
    [
        ("1999 年销售额", "zh", "现有数据中没有 1999 年符合查询条件的记录。"),
        ("GMV in 1999", "en", "The available data has no records matching the query for 1999."),
    ],
)
def test_explicit_year_reports_empty_results_in_the_query_language(question, language, expected):
    gateway = build_gateway(Settings(llm_provider="mock", deepseek_api_key=None))
    graph = build_agent_graph(gateway, sql_runner=lambda sql: _result(sql, ["gmv"], []))

    result = graph.invoke({"question": question})

    assert result["response_language"] == language
    assert result["columns"] == ["gmv"]
    assert result["answer"] == expected
    assert "summarize_result:empty_year" in result["trace"]
