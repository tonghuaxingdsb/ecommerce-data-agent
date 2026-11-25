from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecommerce_agent.agent import build_agent_graph
from ecommerce_agent.eval_cases import CASES
from ecommerce_agent.llm import RuleBasedGateway
from ecommerce_agent.text_to_sql import parse_sql_plan


def test_phase4_dataset_has_100_business_and_10_out_of_scope_cases():
    assert len(CASES) == 110
    assert len({case["id"] for case in CASES}) == 110
    assert len({case["question"] for case in CASES}) == 110
    assert sum(case["outcome"] == "execute" for case in CASES) == 90
    assert sum(case["outcome"] == "refuse" for case in CASES) == 20
    assert sum(case["category"] != "out_of_scope" for case in CASES) == 100
    export_path = Path(__file__).resolve().parents[1] / "data/evaluation/phase4_cases.jsonl"
    exported = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines()]
    assert exported == CASES


@pytest.mark.parametrize("case", [case for case in CASES if case["outcome"] == "refuse"], ids=lambda case: case["id"])
def test_out_of_scope_and_unsupported_requests_never_reach_sql(case):
    def forbidden_sql_runner(sql):
        raise AssertionError(f"Unexpected SQL for {case['id']}: {sql}")

    graph = build_agent_graph(llm=RuleBasedGateway(), sql_runner=forbidden_sql_runner)
    result = graph.invoke({"question": case["question"], "trace": []})

    assert result["query_spec"]["capability"] == case["expected"]["capability"]
    assert result["candidate_sql"] == ""
    assert result.get("row_count", 0) == 0
    assert "prepare_context:default_schema_and_metrics" not in result["trace"]
    assert result["answer"]
    assert not result["answer"].startswith("需要先澄清口径")


def test_out_of_scope_follow_up_does_not_inherit_business_context():
    graph = build_agent_graph(llm=RuleBasedGateway(), sql_runner=lambda sql: (_ for _ in ()).throw(AssertionError(sql)))
    result = graph.invoke({
        "question": "今天天气怎么样？",
        "history": [{"question": "近30天销售额是多少？", "query_spec": {"metrics": ["gmv"], "time_range": "近 30 天"}}],
        "trace": [],
    })

    assert result["query_spec"]["capability"] == "out_of_scope"
    assert result["inherited_query_spec"] == {}
    assert result["candidate_sql"] == ""


def test_out_of_scope_wording_cannot_trigger_legacy_slow_moving_sql():
    graph = build_agent_graph(llm=RuleBasedGateway(), sql_runner=lambda sql: (_ for _ in ()).throw(AssertionError(sql)))
    result = graph.invoke({"question": "写一首关于滞销商品的诗", "trace": []})

    assert result["query_spec"]["capability"] == "out_of_scope"
    assert result["candidate_sql"] == ""


@pytest.mark.parametrize("question", ["那今天天气呢？", "写一段商品文案", "帮我找最便宜的机票"])
def test_additional_out_of_scope_phrasings_do_not_run_sql(question):
    graph = build_agent_graph(llm=RuleBasedGateway(), sql_runner=lambda sql: (_ for _ in ()).throw(AssertionError(sql)))
    result = graph.invoke({"question": question, "trace": []})

    assert result["query_spec"]["capability"] == "out_of_scope"
    assert result["candidate_sql"] == ""


def test_daily_trend_uses_chronological_sql_even_if_model_ranks_by_value():
    plan = parse_sql_plan(
        '{"query_spec":{"metrics":["gmv"],"dimensions":["order_date"]},'
        '"candidate_sql":"SELECT order_date, SUM(amount) AS gmv FROM fact_orders WHERE status = \'paid\' GROUP BY order_date ORDER BY gmv DESC"}',
        "sales_overview",
        "近30天每日销售额趋势",
    )

    assert "ORDER BY order_date ASC" in plan.candidate_sql
    assert plan.query_spec["sort_by"] == "order_date"


def test_slow_moving_product_question_survives_semantic_validation():
    result = build_agent_graph(llm=RuleBasedGateway()).invoke({"question": "近30天哪些商品滞销", "trace": []})

    assert result["query_spec"]["metrics"] == ("units",)
    assert result["query_spec"]["sort_direction"] == "asc"
    assert "ORDER BY units ASC" in result["candidate_sql"]
    assert result.get("semantic_error", "") == ""
    assert "execute_sql:ok" in result["trace"]
