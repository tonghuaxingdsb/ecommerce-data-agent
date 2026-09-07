from __future__ import annotations

import json
from typing import Any

from ecommerce_agent.agent import build_agent_graph
from ecommerce_agent.llm import LLMResult
from ecommerce_agent.sql_tool import SQLExecutionResult


class StubGateway:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def is_configured(self) -> bool:
        return True

    def complete(
        self,
        question: str,
        schema_context: str,
        metric_context: str,
        intent: str,
        previous_sql: str | None = None,
        sql_error: str | None = None,
    ) -> LLMResult:
        self.calls += 1
        sql = "SELECT 1 AS metric_value"
        if self.calls == 1 and "重试" in question:
            sql = "SELECT missing_column FROM fact_orders"
        payload = {
            "intent": intent,
            "needs_clarification": False,
            "clarifying_question": "",
            "analysis_plan": ["生成 SQL", "执行 SQL"],
            "candidate_sql": sql,
            "summary": "stub summary",
            "chart_type": "table",
        }
        return LLMResult(text=json.dumps(payload, ensure_ascii=False), configured=True)

    def summarize(
        self,
        question: str,
        intent: str,
        plan: dict[str, Any],
        columns: list[str],
        rows: list[dict[str, Any]],
        language: str,
    ) -> LLMResult:
        return LLMResult(text="近 30 天销售额表现稳定，这是面向运营的中文分析。", configured=True)


def stub_sql_runner(sql: str) -> SQLExecutionResult:
    if "missing_column" in sql:
        return SQLExecutionResult(
            ok=False,
            sql=sql,
            executable_sql="",
            columns=[],
            rows=[],
            row_count=0,
            elapsed_ms=1,
            error="column missing_column does not exist",
        )
    return SQLExecutionResult(
        ok=True,
        sql=sql,
        executable_sql=f"SELECT * FROM ({sql}) AS agent_limited_result LIMIT 200",
        columns=["metric_value"],
        rows=[{"metric_value": 1}],
        row_count=1,
        elapsed_ms=1,
        error=None,
    )


def test_agent_graph_runs_from_question_to_answer():
    graph = build_agent_graph(StubGateway(), sql_runner=stub_sql_runner)

    result = graph.invoke({"question": "近 30 天销售额多少？", "trace": []})

    assert result["intent"] == "sales_overview"
    assert result["llm_configured"] is True
    assert result["row_count"] == 1
    assert result["rows"] == [{"metric_value": 1}]
    assert "中文分析" in result["answer"]
    assert "SELECT" not in result["answer"]
    assert result["trace"] == [
        "classify_intent:sales_overview",
        "detect_language:zh",
        "prepare_context:default_schema_and_metrics",
        "generate_sql:deepseek",
        "execute_sql:ok",
        "summarize_result:deepseek",
        "finalize:done",
    ]


def test_agent_graph_retries_after_sql_error():
    gateway = StubGateway()
    graph = build_agent_graph(gateway, sql_runner=stub_sql_runner, max_retries=2)

    result = graph.invoke({"question": "重试一下近 30 天销售额", "trace": []})

    assert gateway.calls == 2
    assert result["row_count"] == 1
    assert result["sql_error"] == ""
    assert "react_retry:1" in result["trace"]
