from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ecommerce_agent.conversation import is_ambiguous_metric_question
from ecommerce_agent.semantic import QuerySpec, build_query_spec, compile_query_spec, extract_area_filter
from ecommerce_agent.time_intelligence import build_sales_overview_sql


FEW_SHOTS = """
示例 1：销售趋势
用户：近 30 天销售额多少？环比上个月呢？
SQL：
WITH bounds AS (
  SELECT max(order_date) AS end_date FROM fact_orders
),
periods AS (
  SELECT
    SUM(amount) FILTER (WHERE order_date BETWEEN end_date - INTERVAL '29 days' AND end_date) AS current_gmv,
    COUNT(DISTINCT order_id) FILTER (WHERE order_date BETWEEN end_date - INTERVAL '29 days' AND end_date) AS current_orders,
    SUM(amount) FILTER (WHERE order_date BETWEEN end_date - INTERVAL '59 days' AND end_date - INTERVAL '30 days') AS previous_gmv,
    COUNT(DISTINCT order_id) FILTER (WHERE order_date BETWEEN end_date - INTERVAL '59 days' AND end_date - INTERVAL '30 days') AS previous_orders
  FROM fact_orders CROSS JOIN bounds
  WHERE status = 'paid'
)
SELECT current_gmv, current_orders, previous_gmv, previous_orders,
       ROUND((current_gmv - previous_gmv) / NULLIF(previous_gmv, 0) * 100, 2) AS gmv_mom_pct
FROM periods

示例 2：商品排名
用户：Top10 商品是哪些？
SQL：
SELECT p.product_name, p.category, SUM(o.amount) AS gmv, SUM(o.quantity) AS units, COUNT(DISTINCT o.order_id) AS orders
FROM fact_orders o
JOIN dim_product p ON p.product_id = o.product_id
WHERE o.status = 'paid'
GROUP BY p.product_name, p.category
ORDER BY gmv DESC
LIMIT 10

示例 3：渠道分析
用户：哪个渠道销售额最高？
SQL：
SELECT channel, SUM(amount) AS gmv, COUNT(DISTINCT order_id) AS orders, ROUND(SUM(amount) / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS avg_order_value
FROM fact_orders
WHERE status = 'paid'
GROUP BY channel
ORDER BY gmv DESC
""".strip()


@dataclass(frozen=True)
class SQLPlan:
    intent: str
    needs_clarification: bool = False
    clarifying_question: str = ""
    analysis_plan: list[str] = field(default_factory=list)
    candidate_sql: str = ""
    summary: str = ""
    chart_type: str = "table"
    chart_x: str | None = None
    chart_y: str | None = None
    query_spec: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_sql_plan(
    text: str,
    default_intent: str,
    question: str,
    inherited_query_spec: dict[str, Any] | None = None,
) -> SQLPlan:
    if "滞销" in question:
        return fallback_sql_plan(question, default_intent, inherited_query_spec=inherited_query_spec)
    try:
        payload = json.loads(extract_json(text))
    except json.JSONDecodeError:
        return fallback_sql_plan(question, default_intent, inherited_query_spec=inherited_query_spec)

    raw_spec = payload.get("query_spec") if isinstance(payload.get("query_spec"), dict) else {}
    spec = build_query_spec(question, raw_spec, inherited=inherited_query_spec)
    candidate_sql = str(payload.get("candidate_sql") or "")
    force_compiled = spec.analysis_type == "contribution" or spec.capability != "semantic_sql" or spec.dimensions == ("order_date",)
    compiled = compile_query_spec(spec, question) if not candidate_sql or force_compiled else None
    return SQLPlan(
        intent=_intent_from_spec(spec, str(payload.get("intent") or default_intent)),
        needs_clarification=spec.needs_clarification,
        clarifying_question=spec.clarifying_question if spec.needs_clarification else "",
        analysis_plan=list(payload.get("analysis_plan") or []),
        candidate_sql=(compiled.sql if compiled and force_compiled else candidate_sql) or (compiled.sql if compiled else ""),
        summary=compiled.summary if compiled and force_compiled else str(payload.get("summary") or (compiled.summary if compiled else "")),
        chart_type=compiled.chart_type if compiled and force_compiled else str(payload.get("chart_type") or (compiled.chart_type if compiled else "table")),
        chart_x=compiled.chart_x if compiled and force_compiled else payload.get("chart_x") or (compiled.chart_x if compiled else None),
        chart_y=compiled.chart_y if compiled and force_compiled else payload.get("chart_y") or (compiled.chart_y if compiled else None),
        query_spec=spec.to_dict(),
    )


def extract_json(text: str) -> str:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    return fenced.group(1) if fenced else stripped


def fallback_sql_plan(
    question: str,
    intent: str,
    inherited_query_spec: dict[str, Any] | None = None,
) -> SQLPlan:
    spec = build_query_spec(question, inherited=inherited_query_spec)
    if spec.capability != "semantic_sql":
        if spec.needs_clarification:
            return SQLPlan(
                intent=_intent_from_spec(spec, intent),
                needs_clarification=True,
                clarifying_question=spec.clarifying_question,
                analysis_plan=["识别能力边界和所需数据"],
                summary=spec.clarifying_question,
                query_spec=spec.to_dict(),
            )
        compiled = compile_query_spec(spec, question)
        return SQLPlan(
            intent=_intent_from_spec(spec, intent),
            analysis_plan=default_steps(),
            candidate_sql=compiled.sql,
            summary=compiled.summary,
            chart_type=compiled.chart_type,
            chart_x=compiled.chart_x,
            chart_y=compiled.chart_y,
            query_spec=spec.to_dict(),
        )

    if is_ambiguous_metric_question(question):
        return SQLPlan(
            intent=intent,
            needs_clarification=True,
            clarifying_question="你说的“卖得怎么样”具体想看销售额、订单量、销量还是客单价？",
            analysis_plan=["澄清核心指标", "确定时间范围和筛选条件"],
            summary="用户尚未指定要比较的业务指标。",
        )

    if "滞销" in question:
        sql = """
SELECT p.product_name, p.category, p.stock, COALESCE(SUM(o.quantity), 0) AS units, COALESCE(SUM(o.amount), 0) AS gmv
FROM dim_product p
LEFT JOIN fact_orders o ON o.product_id = p.product_id AND o.status = 'paid' AND o.order_date >= (SELECT max(order_date) - INTERVAL '29 days' FROM fact_orders)
GROUP BY p.product_name, p.category, p.stock
HAVING COALESCE(SUM(o.quantity), 0) < 20 AND p.stock > 500
ORDER BY units ASC, p.stock DESC
LIMIT 10
""".strip()
        spec = build_query_spec("近 30 天销量最低的商品")
        return SQLPlan(
            intent="product_analysis",
            analysis_plan=default_steps(),
            candidate_sql=sql,
            summary="按近 30 天 paid 销量和当前库存识别疑似滞销商品。",
            chart_type="bar",
            chart_x="product_name",
            chart_y="units",
            query_spec=spec.to_dict(),
        )

    if spec.needs_clarification:
        return SQLPlan(
            intent=_intent_from_spec(spec, intent),
            needs_clarification=True,
            clarifying_question=spec.clarifying_question,
            analysis_plan=["识别指标、维度和可用数据", "澄清缺失或不支持的口径"],
            summary=spec.clarifying_question,
            query_spec=spec.to_dict(),
        )

    normalized = question.lower()
    if not spec.dimensions and spec.metrics == ("gmv",) and any(token in normalized for token in ("同比", "环比", "对比", "相比", "增长", "变化")):
        area_filter = extract_area_filter(question)
        sql, time_intent = build_sales_overview_sql(question, area_filter=area_filter)
        comparison_label = "去年同期" if time_intent.comparison == "yoy" else "前一周期"
        return SQLPlan(
            intent="sales_overview",
            analysis_plan=default_steps(),
            candidate_sql=sql,
            summary=f"对比{time_intent.label}与{comparison_label}的 paid GMV、订单量、销量和客单价。",
            chart_type="table",
            query_spec=spec.to_dict(),
        )

    compiled = compile_query_spec(spec, question)
    return SQLPlan(
        intent=_intent_from_spec(spec, intent),
        analysis_plan=default_steps(),
        candidate_sql=compiled.sql,
        summary=compiled.summary,
        chart_type=compiled.chart_type,
        chart_x=compiled.chart_x,
        chart_y=compiled.chart_y,
        query_spec=spec.to_dict(),
    )


def default_steps() -> list[str]:
    return ["识别业务意图和指标口径", "生成只读 SELECT SQL", "执行 SQL 并基于结果返回结论"]


def _intent_from_spec(spec: QuerySpec, default: str) -> str:
    if spec.capability == "inventory_forecast":
        return "inventory_planning"
    if spec.capability.startswith("unsupported_"):
        return spec.task_type
    if any(dimension in spec.dimensions for dimension in ("product", "category")):
        return "product_analysis"
    if "channel" in spec.dimensions:
        return "channel_analysis"
    if "repurchase_rate" in spec.metrics or "vip_level" in spec.dimensions:
        return "user_analysis"
    if "order_date" in spec.dimensions or any(metric in spec.metrics for metric in ("gmv", "orders", "units", "avg_order_value")):
        return "sales_overview"
    return default
