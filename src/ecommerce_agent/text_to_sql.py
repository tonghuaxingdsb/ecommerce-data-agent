from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_sql_plan(text: str, default_intent: str, question: str) -> SQLPlan:
    try:
        payload = json.loads(extract_json(text))
    except json.JSONDecodeError:
        return fallback_sql_plan(question, default_intent)

    return SQLPlan(
        intent=str(payload.get("intent") or default_intent),
        needs_clarification=bool(payload.get("needs_clarification", False)),
        clarifying_question=str(payload.get("clarifying_question") or ""),
        analysis_plan=list(payload.get("analysis_plan") or []),
        candidate_sql=str(payload.get("candidate_sql") or ""),
        summary=str(payload.get("summary") or ""),
        chart_type=str(payload.get("chart_type") or "table"),
        chart_x=payload.get("chart_x"),
        chart_y=payload.get("chart_y"),
    )


def extract_json(text: str) -> str:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    return fenced.group(1) if fenced else stripped


def fallback_sql_plan(question: str, intent: str) -> SQLPlan:
    normalized = question.lower()
    area_filter = extract_area_filter(question)

    if intent == "product_analysis":
        if "滞销" in question:
            sql = """
SELECT p.product_name, p.category, p.stock, COALESCE(SUM(o.quantity), 0) AS units_sold, COALESCE(SUM(o.amount), 0) AS gmv
FROM dim_product p
LEFT JOIN fact_orders o ON o.product_id = p.product_id AND o.status = 'paid' AND o.order_date >= (SELECT max(order_date) - INTERVAL '29 days' FROM fact_orders)
GROUP BY p.product_name, p.category, p.stock
HAVING COALESCE(SUM(o.quantity), 0) < 20 AND p.stock > 500
ORDER BY units_sold ASC, p.stock DESC
LIMIT 20
""".strip()
            summary = "按近 30 天 paid 销量和当前库存识别疑似滞销商品。"
        else:
            sql = """
SELECT p.product_name, p.category, SUM(o.amount) AS gmv, SUM(o.quantity) AS units, COUNT(DISTINCT o.order_id) AS orders
FROM fact_orders o
JOIN dim_product p ON p.product_id = o.product_id
WHERE o.status = 'paid'
GROUP BY p.product_name, p.category
ORDER BY gmv DESC
LIMIT 10
""".strip()
            summary = "按 paid GMV 输出 Top10 商品。"
        return SQLPlan(intent=intent, analysis_plan=default_steps(), candidate_sql=sql, summary=summary, chart_type="bar", chart_x="product_name", chart_y="gmv")

    if intent == "user_analysis" or "复购" in question:
        region_join = "JOIN dim_region r ON r.region_id = o.region_id" if area_filter else ""
        region_where = f"AND r.area = '{area_filter}'" if area_filter else ""
        sql = f"""
WITH user_orders AS (
  SELECT o.user_id, COUNT(DISTINCT o.order_id) AS paid_orders, SUM(o.amount) AS gmv
  FROM fact_orders o
  {region_join}
  WHERE o.status = 'paid'
    {region_where}
  GROUP BY o.user_id
)
SELECT
  COUNT(*) AS paid_users,
  COUNT(*) FILTER (WHERE paid_orders >= 2) AS repeat_users,
  ROUND(COUNT(*) FILTER (WHERE paid_orders >= 2)::numeric / NULLIF(COUNT(*), 0) * 100, 2) AS repurchase_rate_pct,
  ROUND(AVG(gmv), 2) AS avg_user_gmv
FROM user_orders
""".strip()
        return SQLPlan(intent="user_analysis", analysis_plan=default_steps(), candidate_sql=sql, summary="按用户 paid 订单数计算周期内复购率。", chart_type="table")

    if intent == "channel_analysis":
        sql = """
SELECT channel, SUM(amount) AS gmv, COUNT(DISTINCT order_id) AS orders, SUM(quantity) AS units,
       ROUND(SUM(amount) / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS avg_order_value
FROM fact_orders
WHERE status = 'paid'
GROUP BY channel
ORDER BY gmv DESC
""".strip()
        return SQLPlan(intent=intent, analysis_plan=default_steps(), candidate_sql=sql, summary="按渠道聚合 paid GMV、订单量和客单价。", chart_type="bar", chart_x="channel", chart_y="gmv")

    if "类目" in question:
        sql = """
SELECT p.category, SUM(o.amount) AS gmv, SUM(o.quantity) AS units, COUNT(DISTINCT o.order_id) AS orders
FROM fact_orders o
JOIN dim_product p ON p.product_id = o.product_id
WHERE o.status = 'paid'
GROUP BY p.category
ORDER BY gmv DESC
""".strip()
        return SQLPlan(intent="product_analysis", analysis_plan=default_steps(), candidate_sql=sql, summary="按类目聚合 paid GMV、销量和订单量。", chart_type="bar", chart_x="category", chart_y="gmv")

    region_join = "JOIN dim_region r ON r.region_id = o.region_id" if area_filter else ""
    region_where = f"AND r.area = '{area_filter}'" if area_filter else ""
    if any(keyword in normalized for keyword in ["趋势", "每天", "每日"]):
        sql = f"""
SELECT o.order_date, SUM(o.amount) AS gmv, COUNT(DISTINCT o.order_id) AS orders, SUM(o.quantity) AS units
FROM fact_orders o
{region_join}
WHERE o.status = 'paid'
  AND o.order_date >= (SELECT max(order_date) - INTERVAL '29 days' FROM fact_orders)
  {region_where}
GROUP BY o.order_date
ORDER BY o.order_date
""".strip()
        return SQLPlan(intent="sales_overview", analysis_plan=default_steps(), candidate_sql=sql, summary="按天输出近 30 天 paid 销售趋势。", chart_type="line", chart_x="order_date", chart_y="gmv")

    sql = f"""
WITH bounds AS (
  SELECT max(order_date) AS end_date FROM fact_orders
),
periods AS (
  SELECT
    SUM(o.amount) FILTER (WHERE o.order_date BETWEEN end_date - INTERVAL '29 days' AND end_date) AS current_gmv,
    COUNT(DISTINCT o.order_id) FILTER (WHERE o.order_date BETWEEN end_date - INTERVAL '29 days' AND end_date) AS current_orders,
    SUM(o.quantity) FILTER (WHERE o.order_date BETWEEN end_date - INTERVAL '29 days' AND end_date) AS current_units,
    SUM(o.amount) FILTER (WHERE o.order_date BETWEEN end_date - INTERVAL '59 days' AND end_date - INTERVAL '30 days') AS previous_gmv,
    COUNT(DISTINCT o.order_id) FILTER (WHERE o.order_date BETWEEN end_date - INTERVAL '59 days' AND end_date - INTERVAL '30 days') AS previous_orders,
    SUM(o.quantity) FILTER (WHERE o.order_date BETWEEN end_date - INTERVAL '59 days' AND end_date - INTERVAL '30 days') AS previous_units
  FROM fact_orders o
  CROSS JOIN bounds
  {region_join}
  WHERE o.status = 'paid'
    {region_where}
)
SELECT
  current_gmv,
  current_orders,
  current_units,
  previous_gmv,
  previous_orders,
  previous_units,
  ROUND((current_gmv - previous_gmv) / NULLIF(previous_gmv, 0) * 100, 2) AS gmv_mom_pct,
  ROUND(current_gmv / NULLIF(current_orders, 0), 2) AS current_avg_order_value
FROM periods
""".strip()
    return SQLPlan(intent="sales_overview", analysis_plan=default_steps(), candidate_sql=sql, summary="对比近 30 天与前 30 天 paid GMV、订单量、销量和客单价。", chart_type="table")


def default_steps() -> list[str]:
    return ["识别业务意图和指标口径", "生成只读 SELECT SQL", "执行 SQL 并基于结果返回结论"]


def extract_area_filter(question: str) -> str | None:
    for area in ["华东", "华南", "华北", "西南", "华中", "西北", "东北"]:
        if area in question:
            return area
    return None
