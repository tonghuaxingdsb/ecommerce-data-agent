from __future__ import annotations

from ecommerce_agent.charting import build_chart_spec
from ecommerce_agent.capabilities import match_capability
from ecommerce_agent.conversation import ConversationTurn, inherited_query_spec, is_ambiguous_metric_question, is_follow_up_question, resolve_follow_up
from ecommerce_agent.agent import infer_intent
from ecommerce_agent.rag import DictionaryRetriever, retrieve_rag_context
from ecommerce_agent.text_to_sql import fallback_sql_plan
from ecommerce_agent.time_intelligence import build_sales_overview_sql, parse_time_intent


def test_time_intelligence_supports_custom_window_and_yoy():
    intent = parse_time_intent("近 7 天销售额同比去年同期")
    assert intent.window_days == 7
    assert intent.comparison == "yoy"
    sql, _ = build_sales_overview_sql("近 7 天销售额同比去年同期")
    assert "INTERVAL '6 days'" in sql
    assert "INTERVAL '365 days'" in sql
    assert "change_pct" in sql


def test_time_intelligence_uses_calendar_boundaries():
    intent = parse_time_intent("本月销售额环比")
    assert intent.calendar_period == "month"
    sql, _ = build_sales_overview_sql("本月销售额环比")
    assert "date_trunc('month', end_date)" in sql

    intent = parse_time_intent("上周销售额")
    assert intent.calendar_period == "week"
    assert intent.period_offset == -1
    sql, _ = build_sales_overview_sql("上周销售额")
    assert "date_trunc('week', end_date - INTERVAL '1 week')" in sql

    intent = parse_time_intent("上个月销售额")
    assert intent.period_offset == -1
    sql, _ = build_sales_overview_sql("上个月销售额")
    assert "end_date - INTERVAL '1 month'" in sql


def test_calendar_question_is_classified_as_sales_overview():
    assert infer_intent("上周销售额") == "sales_overview"


def test_dictionary_retrieval_returns_metric_and_relevant_fields():
    schema, metrics, sources = retrieve_rag_context("华东区 GMV 和复购率")
    assert "fact_orders.amount" in schema
    assert "复购率" in metrics
    assert sources


def test_pgvector_retriever_is_deterministic_without_database():
    retriever = DictionaryRetriever()
    items = retriever.retrieve("哪个渠道销售额最高？")
    assert items[0].kind in {"data", "metric"}
    assert any("channel" in item.name for item in items)


def test_ambiguous_metric_question_returns_clarification_plan():
    assert is_ambiguous_metric_question("上个月卖得怎么样？") is True
    plan = fallback_sql_plan("上个月卖得怎么样？", "sales_overview")
    assert plan.needs_clarification is True
    assert "销售额" in plan.clarifying_question


def test_follow_up_uses_previous_turn_context():
    result = resolve_follow_up("那环比呢？", [ConversationTurn(question="近 30 天销售额多少？")])
    assert "近 30 天销售额多少" in result


def test_contribution_follow_up_inherits_structured_query_spec():
    previous_spec = {
        "metrics": ["gmv"],
        "dimensions": [],
        "filters": [{"field": "status", "operator": "=", "value": "paid"}],
        "time_range": "近 30 天",
        "comparison": "mom",
    }
    history = [ConversationTurn(question="近 30 天销售额多少？环比上个月呢？", query_spec=previous_spec)]

    effective = resolve_follow_up("主要贡献来源是什么商品", history)

    assert "上一轮问题" in effective
    assert inherited_query_spec("主要贡献来源是什么商品", history) == previous_spec


def test_inventory_planning_is_a_fresh_capability_not_a_follow_up():
    question = "按照趋势下个月各种商品应该准备多少库存量"
    history = [ConversationTurn(question="主要贡献来源是什么商品", query_spec={"metrics": ["gmv"]})]

    decision = match_capability(question)

    assert decision.task_type == "inventory_planning"
    assert decision.capability == "inventory_forecast"
    assert decision.inherit_context is False
    assert is_follow_up_question(question, history) is False
    assert inherited_query_spec(question, history) == {}
    assert resolve_follow_up(question, history) == question


def test_inventory_planning_recognizes_quarter_and_rolling_horizons():
    quarter = match_capability("下季度各商品应该准备多少库存")
    rolling = match_capability("未来45天各商品需要补货多少")

    assert quarter.capability == "inventory_forecast"
    assert quarter.forecast_horizon == "next_calendar_quarter"
    assert rolling.capability == "inventory_forecast"
    assert rolling.forecast_horizon == "next_45_days"


def test_single_character_gai_does_not_make_yinggai_a_follow_up():
    history = [ConversationTurn(question="近 30 天销售额多少？")]

    assert is_follow_up_question("各种商品应该准备多少库存", history) is False


def test_chart_spec_infers_line_chart_for_date_results():
    chart = build_chart_spec({}, ["order_date", "gmv"], [{"order_date": "2026-08-01", "gmv": 10.0}])
    assert chart["type"] == "line"
    assert chart["x"] == "order_date"
    assert chart["y"] == "gmv"
