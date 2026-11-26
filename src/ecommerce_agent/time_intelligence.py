from __future__ import annotations

import re
from dataclasses import dataclass

from ecommerce_agent.dimension_filters import DIMENSION_FILTER_COLUMNS, DIMENSION_FILTER_JOINS, dimension_filter_sql


@dataclass(frozen=True)
class TimeIntent:
    window_days: int = 30
    comparison: str | None = "mom"
    label: str = "近 30 天"
    anchor_sql: str = "(SELECT max(order_date) FROM fact_orders)"
    calendar_period: str | None = None
    period_offset: int = 0
    campaign_day: tuple[int, int] | None = None
    campaign_year: int | None = None
    calendar_year: int | None = None
    explicit: bool = False


_CAMPAIGN_PATTERN = re.compile(r"(?:(?P<year>[12]\d{3})年?)?(?P<event>双十一|双11|(?<![\d近])618(?![\d天日]))")


def campaign_match(question: str) -> re.Match[str] | None:
    return _CAMPAIGN_PATTERN.search(question.replace(" ", ""))


_YEAR_PATTERN = re.compile(r"(?<!\d)([1-9]\d{3})\s*年(?!\s*[0-9０-９一二三四五六七八九十]+\s*月)")
_ENGLISH_YEAR = re.compile(r"\bin\s+([1-9]\d{3})\b(?![-/.]\d)", re.IGNORECASE)
_ENGLISH_RELATIVE = {
    "last month": "上个月",
    "this month": "本月",
    "last week": "上周",
    "this week": "本周",
    "last year": "去年",
    "this year": "今年",
    "yesterday": "昨天",
    "today": "今天",
    "this quarter": "本季度",
    "last quarter": "上季度",
}
_ROLLING_DAYS_PATTERN = re.compile(r"(?:近|最近)\s*(\d+)\s*(?:天|日)")
_ENGLISH_DATE = re.compile(
    r"\b(?:" + "|".join(_ENGLISH_RELATIVE) + r"|(?:last|past) +(?P<days>\d+) +days?)\b"
)
_UNSUPPORTED_DATE = re.compile(
    r"(?<!\d)\d{4}[-/.]\d{1,2}|[0-9０-９一二三四五六七八九十]+\s*月"
    r"|(?:近|最近|过去|未来|接下来)\s*[0-9０-９一二三四五六七八九十]+\s*(?:个?月|周|年|天|日)"
    r"|(?:大前年|前年|明年|前天|明天|上半年|下半年|上个月底|月初|月底|上上周|上上个月)"
    r"|\b(?:last|past|next|previous|this|current)\s+(?:(?:\d+|[a-z]+)\s+)?(?:days?|weeks?|months?|quarters?|years?)\b"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|q[1-4])\b"
    r"|\b(?:in|since|before|after|during|from)\s+\d{4}\b"
    r"|\b(?:january|february|march|april|june|july|august|september|october|november|december)\b"
    r"|\bmay\s+\d{1,4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
)


def explicit_calendar_year(question: str) -> int | None:
    match = _YEAR_PATTERN.search(question) or _ENGLISH_YEAR.search(question)
    return int(match[1]) if match else None


def needs_time_clarification(question: str) -> bool:
    text = " ".join(question.lower().split())
    # A named campaign can include its year. Count it as one expression.
    without_campaigns = re.sub(r"(?:[12]\d{3}\s*年)?\s*(?:双十一|双11|(?<![\d近])618(?![\d天日]))", "", text)
    explicit_count = sum(
        len(pattern.findall(without_campaigns))
        for pattern in (_ENGLISH_DATE, _ENGLISH_YEAR, _YEAR_PATTERN, _ROLLING_DAYS_PATTERN)
    )
    if explicit_count > 1 or (explicit_count and without_campaigns != text):
        return True
    remaining = _ENGLISH_YEAR.sub("", _ENGLISH_DATE.sub("", without_campaigns))
    return bool(_UNSUPPORTED_DATE.search(_ROLLING_DAYS_PATTERN.sub("", remaining)))


def parse_time_intent(question: str) -> TimeIntent:
    english_text = " ".join(question.lower().split())
    english_date = _ENGLISH_DATE.search(english_text)
    if english_date:
        translated = f"近 {english_date['days']} 天" if english_date["days"] else _ENGLISH_RELATIVE[english_date[0]]
        question = f"{translated} {question}"
    text = re.sub(r"\s+", "", question.lower())
    day_match = _ROLLING_DAYS_PATTERN.search(text)
    calendar_year = explicit_calendar_year(question)
    explicit = True
    calendar_period: str | None = None
    period_offset = 0
    event = campaign_match(text)
    campaign_day = None
    campaign_year = None
    if event:
        name = event["event"]
        campaign_day = (6, 18) if name == "618" else (11, 11)
        campaign_year = int(event["year"]) if event["year"] else None
        days, label = 1, "618" if name == "618" else "双十一"
        if campaign_year:
            label = f"{campaign_year}年{label}"
    elif calendar_year:
        days, label = 365, f"{calendar_year}年"
        calendar_period = "year"
    elif day_match:
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
    elif "去年" in text and "去年同期" not in text:
        days, label = 365, "去年"
        calendar_period, period_offset = "year", -1
    elif "上季度" in text or "本季度" in text or "这个季度" in text:
        days, label = 90, "上季度" if "上季度" in text else "本季度"
        calendar_period, period_offset = "quarter", -1 if label == "上季度" else 0
    elif "昨天" in text or "今天" in text:
        days, label = 1, "昨天" if "昨天" in text else "今天"
        calendar_period, period_offset = "day", -1 if label == "昨天" else 0
    elif "今年" in text or "年度" in text:
        days, label = 365, "今年"
        calendar_period = "year"
    else:
        days, label = 30, "近 30 天"
        explicit = False

    if "同比" in text or "去年同期" in text:
        comparison = "yoy"
    elif any(token in text for token in ("环比", "对比", "相比", "增长", "变化")):
        comparison = "mom"
    else:
        comparison = None
    return TimeIntent(
        window_days=days, comparison=comparison, label=label, calendar_period=calendar_period,
        period_offset=period_offset, campaign_day=campaign_day, campaign_year=campaign_year,
        calendar_year=calendar_year, explicit=explicit,
    )


def build_period_sql(time_intent: TimeIntent) -> str:
    """Return reusable current/comparison predicates anchored to dataset max date."""
    current, comparison = build_period_predicates(time_intent)
    return f"current_period: {current}\ncomparison_period: {comparison}"


def build_period_predicates(time_intent: TimeIntent, date_column: str = "o.order_date") -> tuple[str, str]:
    """Build current and comparison predicates for reusable dimensional analysis."""
    if time_intent.campaign_day:
        month, day = time_intent.campaign_day
        if time_intent.campaign_year:
            event_date = f"make_date({time_intent.campaign_year}, {month}, {day})"
        else:
            year = "EXTRACT(YEAR FROM end_date)::int"
            this_year = f"make_date({year}, {month}, {day})"
            event_date = f"(CASE WHEN {this_year} <= end_date THEN {this_year} ELSE make_date({year} - 1, {month}, {day}) END)"
        interval = "1 year" if time_intent.comparison == "yoy" else "1 day"
        return f"{date_column} = {event_date}", f"{date_column} = ({event_date} - INTERVAL '{interval}')::date"
    days = time_intent.window_days
    anchor = "end_date"
    if time_intent.calendar_year:
        anchor = f"make_date({time_intent.calendar_year}, 1, 1)"
    elif time_intent.period_offset:
        unit = "3 months" if time_intent.calendar_period == "quarter" else f"{abs(time_intent.period_offset)} {time_intent.calendar_period}"
        anchor = f"end_date - INTERVAL '{unit}'"
    if time_intent.calendar_period in {"day", "quarter"}:
        period = time_intent.calendar_period
        unit = "1 day" if period == "day" else "3 months"
        previous_interval = "1 year" if time_intent.comparison == "yoy" else unit
        start = f"date_trunc('{period}', {anchor})"
        previous = f"date_trunc('{period}', {anchor} - INTERVAL '{previous_interval}')"
        return (
            f"{date_column} >= {start}::date AND {date_column} < {start} + INTERVAL '{unit}'",
            f"{date_column} >= {previous}::date AND {date_column} < {previous} + INTERVAL '{unit}'",
        )
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


def build_sales_overview_sql(
    question: str,
    *,
    area_filter: str | None = None,
    time_intent: TimeIntent | None = None,
    dimension_filters: dict[str, str] | None = None,
) -> tuple[str, TimeIntent]:
    intent = time_intent or parse_time_intent(question)
    current_predicate, comparison_predicate = build_period_predicates(intent)
    filters = dict(dimension_filters or {})
    if area_filter:
        filters["area"] = area_filter
    filter_joins = "\n  ".join(dict.fromkeys(DIMENSION_FILTER_JOINS[key] for key in filters if key in DIMENSION_FILTER_JOINS))
    filter_where = "".join(
        f" AND {dimension_filter_sql(field, value)}" for field, value in filters.items() if field in DIMENSION_FILTER_COLUMNS
    )
    sql = f"""
WITH bounds AS (
  SELECT max(order_date) AS end_date FROM fact_orders
), periods AS (
  SELECT
    SUM(o.amount) FILTER (WHERE {current_predicate}) AS current_gmv,
    COUNT(DISTINCT o.order_id) FILTER (WHERE {current_predicate}) AS current_orders,
    SUM(o.quantity) FILTER (WHERE {current_predicate}) AS current_units,
    SUM(o.amount) FILTER (WHERE {comparison_predicate}) AS previous_gmv,
    COUNT(DISTINCT o.order_id) FILTER (WHERE {comparison_predicate}) AS previous_orders,
    SUM(o.quantity) FILTER (WHERE {comparison_predicate}) AS previous_units
  FROM fact_orders o CROSS JOIN bounds
  {filter_joins}
  WHERE o.status = 'paid'{filter_where}
), changes AS (
  SELECT *, ROUND((current_gmv - previous_gmv) / NULLIF(previous_gmv, 0) * 100, 2) AS change_pct
  FROM periods
)
SELECT current_gmv, current_orders, current_units, previous_gmv, previous_orders, previous_units,
       change_pct, change_pct AS gmv_mom_pct,
       ROUND(current_gmv / NULLIF(current_orders, 0), 2) AS current_avg_order_value
FROM changes
""".strip()
    return sql, intent
