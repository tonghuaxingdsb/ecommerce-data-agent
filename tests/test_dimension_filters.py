from __future__ import annotations

import pytest

from ecommerce_agent.semantic import build_query_spec, compile_query_spec, validate_sql_semantics
from ecommerce_agent.text_to_sql import fallback_sql_plan


@pytest.mark.parametrize(
    ("question", "field", "value", "predicate", "join"),
    [
        ("搜索渠道的销售额是多少", "channel", "搜索", "o.channel = '搜索'", None),
        ("美妆类目的销量是多少", "category", "美妆个护", "p.category = '美妆个护'", "JOIN dim_product p"),
        ("黑金会员的客单价是多少", "vip_level", "黑金", "u.vip_level = '黑金'", "JOIN dim_user u"),
        ("直播渠道的复购率", "channel", "直播", "o.channel = '直播'", None),
        ("品牌为星野的销售额", "brand", "星野", "p.brand = '星野'", "JOIN dim_product p"),
    ],
)
def test_dimension_values_scope_the_query(question, field, value, predicate, join):
    plan = fallback_sql_plan(question, "general_analysis")
    spec = build_query_spec(question, {"dimensions": [field]})

    assert not plan.needs_clarification
    assert plan.query_spec["dimensions"] == spec.dimensions == ()
    assert {"field": field, "operator": "=", "value": value} in plan.query_spec["filters"]
    assert predicate in plan.candidate_sql
    if join:
        assert join in plan.candidate_sql
    assert validate_sql_semantics(spec, plan.candidate_sql).ok


def test_dimension_scopes_coexist_with_grouping_and_deduplicate_the_product_join():
    question = "华东地区星野品牌和美妆品类的销售额按渠道排行"
    spec = build_query_spec(question)
    compiled = compile_query_spec(spec, question)

    assert spec.dimensions == ("channel",)
    assert "GROUP BY o.channel" in compiled.sql
    assert "r.area = '华东'" in compiled.sql
    assert "p.category = '美妆个护'" in compiled.sql
    assert "p.brand = '星野'" in compiled.sql
    assert compiled.sql.count("JOIN dim_product p") == 1


def test_explicit_scope_overrides_inherited_scope_and_preserves_other_filters():
    inherited = build_query_spec("华东地区直播渠道的销售额").to_dict()
    question = "搜索渠道呢"
    spec = build_query_spec(question, inherited=inherited)
    compiled = compile_query_spec(spec, question)

    assert spec.metrics == ("gmv",)
    assert "o.channel = '搜索'" in compiled.sql
    assert "r.area = '华东'" in compiled.sql
    assert "直播" not in compiled.sql


def test_scope_and_explicit_grouping_can_use_the_same_dimension():
    assert build_query_spec("搜索渠道销售额按渠道看").dimensions == ("channel",)


def test_quoted_brand_name_round_trips_as_one_literal():
    question = "品牌为“Nature's Way”的销量"
    spec = build_query_spec(question)
    compiled = compile_query_spec(spec, question)

    assert "p.brand = 'Nature''s Way'" in compiled.sql
    assert validate_sql_semantics(spec, compiled.sql).ok


def test_dimension_filter_validation_checks_where_predicate():
    spec = build_query_spec("搜索渠道的销售额是多少")
    sql = "SELECT SUM(o.amount) AS gmv, '搜索' AS note FROM fact_orders o WHERE o.status = 'paid'"

    assert not validate_sql_semantics(spec, sql).ok
    assert not validate_sql_semantics(spec, sql + " AND (o.channel = '搜索' OR o.channel = '直播')").ok
    assert validate_sql_semantics(spec, sql + " AND (o.channel = '搜索' AND o.user_id > 0)").ok


def test_conflicting_values_ask_for_clarification():
    plan = fallback_sql_plan("渠道为搜索和直播渠道的销售额", "general_analysis")

    assert plan.needs_clarification
    assert "渠道" in plan.clarifying_question
    assert not plan.candidate_sql


def test_dimension_filters_apply_to_both_sides_of_sales_comparison():
    question = "星野品牌本月销售额同比"
    plan = fallback_sql_plan(question, "sales_overview")
    spec = build_query_spec(question, plan.query_spec)

    assert "p.brand = '星野'" in plan.candidate_sql
    assert "JOIN dim_product p" in plan.candidate_sql
    assert validate_sql_semantics(spec, plan.candidate_sql).ok
