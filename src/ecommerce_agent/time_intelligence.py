from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TimeIntent:
    window_days: int = 30
    comparison: str | None = "mom"
    label: str = "近 30 天"
    anchor_sql: str = "(SELECT max(order_date) FROM fact_orders)"
    calendar_period: str | None = None
    period_offset: int = 0


def parse_time_intent(question: str) -> TimeIntent:
    text = question.lower().replace(" ", "")
    day_match = re.search(r"(?:近|最近)(\d+)(?:天|日)", text)
    calendar_period: str | None = None
    period_offset = 0
    if day_match:
        days = max(1, min(int(day_match.group(1)), 366))
        label = f"近 {days} 天"
    elif "上周" in text or "上星期" in text:
        days, label = 7, "上周"
        calendar_period = "week"
        period_offset = -1
    elif "本周" in text or "这周" in text or "这个星期" in text:
        days, label = 7, "本周"
        calendar_period = "week"
    elif "本月" in text or "这个月" in text:
        days, label = 30, "本月"
        calendar_period = "month"
    elif "上月" in text or "上个月" in text:
        days, label = 30, "上个月"
        calendar_period = "month"
        period_offset = -1
    elif "今年" in text or "年度" in text:
        days, label = 365, "今年"
        calendar_period = "year"
    else:
        days, label = 30, "近 30 天"
        calendar_period = None

    if "同比" in text or "去年同期" in text:
        comparison = "yoy"
    elif any(token in text for token in ("环比", "对比", "相比", "增长", "变化")):
        comparison = "mom"
    else:
        comparison = None
    return TimeIntent(window_days=days, comparison=comparison, label=label, calendar_period=calendar_period, period_offset=period_offset)


def build_period_sql(time_intent: TimeIntent) -> str:
    """Return reusable current/comparison predicates anchored to dataset max date."""
    days = time_intent.window_days
    current = f"o.order_date BETWEEN end_date - INTERVAL '{days - 1} days' AND end_date"
    if time_intent.comparison == "yoy":
        comparison = f"o.order_date BETWEEN end_date - INTERVAL '{days + 364} days' AND end_date - INTERVAL '365 days'"
    else:
        comparison = f"o.order_date BETWEEN end_date - INTERVAL '{(days * 2) - 1} days' AND end_date - INTERVAL '{days} days'"
    return f"current_period: {current}\ncomparison_period: {comparison}"


def build_period_predicates(time_intent: TimeIntent, date_column: str = "o.order_date") -> tuple[str, str]:
    """Build current and comparison predicates for reusable dimensional analysis."""
    days = time_intent.window_days
    anchor = "end_date"
    if time_intent.period_offset:
        unit = {"month": "month", "week": "week", "year": "year"}[time_intent.calendar_period or "month"]
        anchor = f"end_date - INTERVAL '{abs(time_intent.period_offset)} {unit}'"
    if time_intent.calendar_period == "month":
        current = f"{date_column} >= date_trunc('month', {anchor})::date AND {date_column} < date_trunc('month', {anchor}) + INTERVAL '1 month'"
        comparison_anchor = f"{anchor} - INTERVAL '{'1 year' if time_intent.comparison == 'yoy' else '1 month'}'"
        comparison = f"{date_column} >= date_trunc('month', {comparison_anchor})::date AND {date_column} < date_trunc('month', {comparison_anchor}) + INTERVAL '1 month'"
    elif time_intent.calendar_period == "week":
        current = f"{date_column} >= date_trunc('week', {anchor})::date AND {date_column} < date_trunc('week', {anchor}) + INTERVAL '1 week'"
        comparison_anchor = f"{anchor} - INTERVAL '{'1 year' if time_intent.comparison == 'yoy' else '7 days'}'"
        comparison = f"{date_column} >= date_trunc('week', {comparison_anchor})::date AND {date_column} < date_trunc('week', {comparison_anchor}) + INTERVAL '1 week'"
    elif time_intent.calendar_period == "year":
        current = f"{date_column} >= date_trunc('year', {anchor})::date AND {date_column} < date_trunc('year', {anchor}) + INTERVAL '1 year'"
        comparison_anchor = f"{anchor} - INTERVAL '1 year'"
        comparison = f"{date_column} >= date_trunc('year', {comparison_anchor})::date AND {date_column} < date_trunc('year', {comparison_anchor}) + INTERVAL '1 year'"
    else:
        current = f"{date_column} BETWEEN end_date - INTERVAL '{days - 1} days' AND end_date"
        if time_intent.comparison == "yoy":
            comparison = f"{date_column} BETWEEN end_date - INTERVAL '{days + 364} days' AND end_date - INTERVAL '365 days'"
        else:
            comparison = f"{date_column} BETWEEN end_date - INTERVAL '{(days * 2) - 1} days' AND end_date - INTERVAL '{days} days'"
    return current, comparison


def build_sales_overview_sql(question: str, *, area_filter: str | None = None) -> tuple[str, TimeIntent]:
    intent = parse_time_intent(question)
    current_predicate, comparison_predicate = build_period_predicates(intent)
    region_join = "JOIN dim_region r ON r.region_id = o.region_id" if area_filter else ""
    region_where = f"AND r.area = '{area_filter}'" if area_filter else ""
    comparison_alias = "previous" if intent.comparison != "yoy" else "same_period_last_year"
    sql = f"""
WITH bounds AS (
  SELECT max(order_date) AS end_date FROM fact_orders
), periods AS (
  SELECT
    SUM(o.amount) FILTER (WHERE {current_predicate}) AS current_gmv,
    COUNT(DISTINCT o.order_id) FILTER (WHERE {current_predicate}) AS current_orders,
    SUM(o.quantity) FILTER (WHERE {current_predicate}) AS current_units,
    SUM(o.amount) FILTER (WHERE {comparison_predicate}) AS {comparison_alias}_gmv,
    COUNT(DISTINCT o.order_id) FILTER (WHERE {comparison_predicate}) AS {comparison_alias}_orders,
    SUM(o.quantity) FILTER (WHERE {comparison_predicate}) AS {comparison_alias}_units
  FROM fact_orders o CROSS JOIN bounds
  {region_join}
  WHERE o.status = 'paid' {region_where}
)
SELECT current_gmv, current_orders, current_units,
       {comparison_alias}_gmv AS previous_gmv,
       {comparison_alias}_orders AS previous_orders,
       {comparison_alias}_units AS previous_units,
       ROUND((current_gmv - {comparison_alias}_gmv) / NULLIF({comparison_alias}_gmv, 0) * 100, 2) AS change_pct,
       ROUND((current_gmv - {comparison_alias}_gmv) / NULLIF({comparison_alias}_gmv, 0) * 100, 2) AS gmv_mom_pct,
       ROUND(current_gmv / NULLIF(current_orders, 0), 2) AS current_avg_order_value
FROM periods
""".strip()
    return sql, intent
