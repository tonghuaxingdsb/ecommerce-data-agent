from __future__ import annotations

import json
from typing import Any

from ecommerce_agent.agent import build_agent_graph, stream_agent_events
from ecommerce_agent.llm import LLMResult
from ecommerce_agent.sql_tool import SQLExecutionResult
from ecommerce_agent.text_to_sql import fallback_sql_plan


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
        sql = """
WITH bounds AS (SELECT max(order_date) AS end_date FROM fact_orders)
SELECT SUM(o.amount) AS gmv
FROM fact_orders o CROSS JOIN bounds
WHERE o.status = 'paid' AND o.order_date BETWEEN end_date - INTERVAL '29 days' AND end_date
""".strip()
        if self.calls == 1 and "重试" in question:
            sql = sql.replace("SUM(o.amount) AS gmv", "SUM(o.amount) AS gmv, missing_column")
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
        columns=["gmv"],
        rows=[{"gmv": 1}],
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
    assert result["rows"] == [{"gmv": 1}]
    assert "中文分析" in result["answer"]
    assert "SELECT" not in result["answer"]
    assert result["trace"] == [
        "classify_intent:sales_overview",
        "detect_language:zh",
        "prepare_context:default_schema_and_metrics",
        "generate_sql:deepseek",
        "validate_semantics:ok",
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


def test_agent_graph_streams_trace_and_final_result():
    events = list(stream_agent_events({"question": "近 30 天销售额", "trace": []}, llm=StubGateway(), sql_runner=stub_sql_runner))
    assert events[0]["event"] == "trace"
    assert events[-1]["event"] == "result"
    assert events[-1]["result"]["row_count"] == 1


def test_agent_graph_retries_semantically_wrong_sql_before_execution():
    class SemanticRepairGateway(StubGateway):
        def complete(self, question, schema_context, metric_context, intent, previous_sql=None, sql_error=None):
            self.calls += 1
            if self.calls == 1:
                sql = """
SELECT p.product_name, SUM(o.amount) AS gmv
FROM fact_orders o JOIN dim_product p ON p.product_id = o.product_id
WHERE o.status = 'paid'
GROUP BY p.product_name
ORDER BY gmv DESC
""".strip()
            else:
                sql = fallback_sql_plan(question, intent).candidate_sql
            payload = {
                "intent": intent,
                "needs_clarification": False,
                "analysis_plan": ["识别复购率", "按商品聚合"],
                "query_spec": {
                    "metrics": ["repurchase_rate"],
                    "dimensions": ["product"],
                    "sort_by": "repurchase_rate",
                    "sort_direction": "desc",
                    "limit": 10,
                },
                "candidate_sql": sql,
                "summary": "按商品计算复购率。",
                "chart_type": "bar",
                "chart_x": "product_name",
                "chart_y": "repurchase_rate_pct",
            }
            return LLMResult(text=json.dumps(payload, ensure_ascii=False), configured=True)

    def repurchase_runner(sql: str) -> SQLExecutionResult:
        assert "repurchase_rate_pct" in sql
        return SQLExecutionResult(
            ok=True,
            sql=sql,
            executable_sql=sql,
            columns=["product_name", "paid_users", "repeat_users", "repurchase_rate_pct"],
            rows=[{"product_name": "测试商品", "paid_users": 100, "repeat_users": 20, "repurchase_rate_pct": 20.0}],
            row_count=1,
            elapsed_ms=1,
            error=None,
        )

    gateway = SemanticRepairGateway()
    result = build_agent_graph(gateway, sql_runner=repurchase_runner).invoke({"question": "什么商品的复购率高", "trace": []})

    assert gateway.calls == 2
    assert any(item.startswith("validate_semantics:error:") for item in result["trace"])
    assert "react_retry:1" in result["trace"]
    assert "validate_semantics:ok" in result["trace"]
    assert result["rows"][0]["repurchase_rate_pct"] == 20.0
