from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable

from ecommerce_agent.capabilities import match_capability
from ecommerce_agent.dimension_filters import (
    DIMENSION_FILTER_COLUMNS,
    DIMENSION_FILTER_JOINS,
    DIMENSION_FILTER_LABELS,
    dimension_filter_equalities,
    dimension_filter_sql,
    dimension_filter_value,
    extract_dimension_filters,
)
from ecommerce_agent.language import detect_response_language
from ecommerce_agent.time_intelligence import (
    build_period_predicates,
    campaign_match,
    needs_time_clarification,
    parse_time_intent,
)


SUPPORTED_METRICS = {
    "gmv": "销售额/GMV",
    "orders": "订单量",
    "refunded_orders": "退款订单数",
    "registered_users": "新增用户数",
    "users": "用户数",
    "units": "销量",
    "avg_order_value": "客单价",
    "repurchase_rate": "复购率",
}

SUPPORTED_DIMENSIONS = {
    "product": "商品",
    "category": "类目",
    "channel": "渠道",
    "area": "地区",
    "vip_level": "会员等级",
    "order_date": "日期",
    "order_month": "月份",
}

METRIC_RESULT_COLUMNS = {
    "gmv": "gmv",
    "orders": "orders",
    "refunded_orders": "refunded_orders",
    "registered_users": "registered_users",
    "users": "users",
    "units": "units",
    "avg_order_value": "avg_order_value",
    "repurchase_rate": "repurchase_rate_pct",
}

DIMENSION_RESULT_COLUMNS = {
    "product": "product_name",
    "category": "category",
    "channel": "channel",
    "area": "area",
    "vip_level": "vip_level",
    "order_date": "order_date",
    "order_month": "order_month",
}

_METRIC_ALIASES = {
    "gmv": "gmv",
    "销售额": "gmv",
    "成交额": "gmv",
    "金额": "gmv",
    "orders": "orders",
    "order_count": "orders",
    "refunded_orders": "refunded_orders",
    "退款订单数": "refunded_orders",
    "registered_users": "registered_users",
    "新增用户数": "registered_users",
    "users": "users",
    "用户数": "users",
    "订单量": "orders",
    "订单数": "orders",
    "units": "units",
    "销量": "units",
    "件数": "units",
    "avg_order_value": "avg_order_value",
    "aov": "avg_order_value",
    "客单价": "avg_order_value",
    "repurchase_rate": "repurchase_rate",
    "repurchase_rate_pct": "repurchase_rate",
    "复购": "repurchase_rate",
    "复购率": "repurchase_rate",
}

_DIMENSION_ALIASES = {
    "product": "product",
    "product_name": "product",
    "商品": "product",
    "sku": "product",
    "category": "category",
    "类目": "category",
    "品类": "category",
    "channel": "channel",
    "渠道": "channel",
    "area": "area",
    "地区": "area",
    "区域": "area",
    "大区": "area",
    "vip_level": "vip_level",
    "会员等级": "vip_level",
    "order_date": "order_date",
    "日期": "order_date",
    "每日": "order_date",
    "order_month": "order_month",
    "月份": "order_month",
    "按月": "order_month",
    "月度": "order_month",
}

_AREA_NAMES = ("华东", "华南", "华北", "西南", "华中", "西北", "东北")
_ENGLISH_GROUPS = {
    "channel": "channel",
    "category": "category",
    "region": "area",
    "product": "product",
    "membership level": "vip_level",
    "vip level": "vip_level",
    "day": "order_date",
    "month": "order_month",
}
_ENGLISH_GROUP_WORDS = r"[a-z]+(?:\s+level)?"
_ENGLISH_GROUP = re.compile(
    rf"\bby\s+({_ENGLISH_GROUP_WORDS}(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+){_ENGLISH_GROUP_WORDS})*)",
    re.IGNORECASE,
)
USER_METRICS = {"registered_users", "users"}
_SELL_THROUGH_RANKING = ("卖得最差", "卖得最少", "卖得最好", "卖得最多")
_RANKING_MARKERS = ("最高", "最低", "top", "前", "排名", "排行", "哪些", "什么", "更高", *_SELL_THROUGH_RANKING)


@dataclass(frozen=True)
class QueryFilter:
    field: str
    operator: str
    value: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> QueryFilter | None:
        field_name = str(value.get("field") or "").strip()
        operator = str(value.get("operator") or "=").strip()
        filter_value = str(value.get("value") or "").strip()
        if not field_name or not filter_value:
            return None
        return cls(field=field_name, operator=operator, value=filter_value)


@dataclass(frozen=True)
class QuerySpec:
    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    filters: tuple[QueryFilter, ...] = ()
    time_range: str | None = None
    sort_by: str | None = None
    sort_direction: str = "desc"
    limit: int | None = None
    minimum_sample_size: int | None = None
    analysis_type: str = "aggregate"
    comparison: str | None = None
    task_type: str = "descriptive_analysis"
    capability: str = "semantic_sql"
    training_window_days: int | None = None
    forecast_horizon: str | None = None
    safety_stock_days: int | None = None
    assumptions: tuple[str, ...] = ()
    needs_clarification: bool = False
    clarifying_question: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    summary: str
    chart_type: str = "table"
    chart_x: str | None = None
    chart_y: str | None = None


@dataclass(frozen=True)
class SemanticValidation:
    ok: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def message(self) -> str:
        return "；".join(self.errors)


def build_query_spec(
    question: str,
    payload: dict[str, Any] | None = None,
    inherited: dict[str, Any] | None = None,
) -> QuerySpec:
    """Ground an LLM plan against explicit metric/dimension words in the question.

    The model is free to infer omitted slots, while explicit user constraints always
    win.  This prevents a fluent but semantically different plan from silently
    replacing "复购率" with GMV or dropping a requested dimension.
    """

    payload = payload or {}
    inherited = inherited or {}
    capability = match_capability(question)
    if capability.capability == "inventory_forecast":
        return QuerySpec(
            metrics=("units",),
            dimensions=("product",),
            filters=(QueryFilter(field="status", operator="=", value="paid"),),
            analysis_type="inventory_plan",
            task_type=capability.task_type,
            capability=capability.capability,
            training_window_days=90,
            forecast_horizon=capability.forecast_horizon or "next_30_days",
            safety_stock_days=7,
            assumptions=(
                "使用最近 90 天逐日 paid 销量的线性趋势作为基线预测",
                "安全库存按预测日均销量的 7 天计算",
                "当前数据没有在途库存和采购提前期，暂按 0 处理",
            ),
        )
    if not capability.supported:
        return QuerySpec(
            task_type=capability.task_type,
            capability=capability.capability,
            needs_clarification=True,
            clarifying_question=(
                f"{capability.reason}。请改问‘按趋势估算下个月各商品应补多少库存’，"
                "或先补充对应预测目标的数据与预测工具。"
                if capability.capability == "unsupported_forecast" else capability.reason
            ),
        )
    supplied_metrics = _normalize_many(payload.get("metrics"), _METRIC_ALIASES)
    supplied_dimensions = _normalize_many(payload.get("dimensions"), _DIMENSION_ALIASES)
    inherited_metrics = _normalize_many(inherited.get("metrics"), _METRIC_ALIASES)
    inherited_dimensions = _normalize_many(inherited.get("dimensions"), _DIMENSION_ALIASES)
    explicit_metrics, unsupported_metric = _detect_metrics(question)
    explicit_filters, grouping_question, ambiguous_filters = extract_dimension_filters(question)
    explicit_dimensions = _detect_dimensions(grouping_question)
    supplied_dimensions = tuple(dimension for dimension in supplied_dimensions if dimension not in explicit_filters)
    inherited_dimensions = tuple(dimension for dimension in inherited_dimensions if dimension not in explicit_filters)

    metrics = explicit_metrics or supplied_metrics or inherited_metrics
    dimensions = explicit_dimensions or supplied_dimensions or inherited_dimensions
    if not explicit_dimensions and {"registered_users", "refunded_orders"}.intersection(explicit_metrics):
        dimensions = inherited_dimensions
    if not metrics and not unsupported_metric and _can_default_to_gmv(question, dimensions):
        metrics = ("gmv",)

    filters = _merge_filters(_payload_filters(inherited.get("filters")), _payload_filters(payload.get("filters")))
    filters = _merge_filters(
        filters,
        tuple(QueryFilter(field=field, operator="=", value=value) for field, value in explicit_filters.items()),
    )
    filters = tuple(item for item in filters if item.field != "status")
    if not USER_METRICS.intersection(metrics):
        status = "refunded" if "refunded_orders" in metrics else "paid"
        filters = (*filters, QueryFilter(field="status", operator="=", value=status))

    monthly = "order_month" in dimensions
    time_intent = parse_time_intent(question)
    time_range = (
        (time_intent.label if time_intent.explicit else None)
        or (None if monthly else _optional_string(payload.get("time_range")))
        or _optional_string(inherited.get("time_range"))
    )
    if "order_date" in dimensions and not time_range:
        time_range = "近 30 天"

    analysis_type = (
        _detect_analysis_type(question)
        or _normalize_analysis_type(payload.get("analysis_type"))
        or _normalize_analysis_type(inherited.get("analysis_type"))
        or "aggregate"
    )
    comparison = (
        _detect_comparison(question)
        or _normalize_comparison(payload.get("comparison"))
        or _normalize_comparison(inherited.get("comparison"))
    )
    if analysis_type == "contribution" and not comparison:
        comparison = "mom"

    sort_by = _normalize_metric(payload.get("sort_by")) or _normalize_metric(inherited.get("sort_by"))
    if metrics and (_is_ranking_question(question) or dimensions):
        sort_by = metrics[0]
    direction = "asc" if any(token in question.lower() for token in ("最低", "最少", "最差", "倒数", "滞销")) else "desc"
    if str(payload.get("sort_direction") or "").lower() in {"asc", "desc"} and not _is_ranking_question(question):
        direction = str(payload["sort_direction"]).lower()
    if dimensions in {("order_date",), ("order_month",)} and not _is_ranking_question(question):
        sort_by = dimensions[0]
        direction = "asc"

    limit = _extract_limit(question)
    if limit is None and "product" in dimensions and any(marker in question for marker in _SELL_THROUGH_RANKING) and "哪个" in question:
        limit = 1
    if limit is None:
        candidate_limit = payload.get("limit")
        if isinstance(candidate_limit, int) and (not monthly or _is_ranking_question(question)):
            limit = max(1, min(candidate_limit, 200))
        elif isinstance(inherited.get("limit"), int) and not explicit_dimensions:
            limit = max(1, min(int(inherited["limit"]), 200))
        elif dimensions and _is_ranking_question(question):
            limit = 10

    minimum_sample_size = payload.get("minimum_sample_size")
    if not isinstance(minimum_sample_size, int) or minimum_sample_size < 1:
        minimum_sample_size = 20 if "repurchase_rate" in metrics and dimensions else None

    clarification = ""
    if unsupported_metric == "conversion_rate":
        clarification = "当前数据只有订单，没有曝光、访问或点击数据，无法计算转化率。请补充漏斗数据或改问销售额、订单量等已有指标。"
    elif unsupported_metric == "profit":
        clarification = "当前数据没有成本字段，无法计算利润或毛利率。请补充成本数据或改问销售额。"
    elif needs_time_clarification(question):
        clarification = (
            "请明确要查询的时间范围，例如上个月、近 7 天或 2026 年。"
            if detect_response_language(question) == "zh"
            else "Please specify a supported time range, such as last month or the last 7 days."
        )
    elif any(group not in _ENGLISH_GROUPS for group in _english_group_labels(question)):
        clarification = (
            "请明确要分组的维度，例如渠道、类目或地区。"
            if detect_response_language(question) == "zh"
            else "Please choose a supported breakdown, such as channel, category, or region."
        )
    elif ambiguous_filters:
        labels = "、".join(DIMENSION_FILTER_LABELS[field] for field in ambiguous_filters)
        clarification = f"请为每个筛选维度提供一个值：{labels}。"
    elif not campaign_match(time_range or "") and any(word in question for word in ("大促", "活动日")):
        clarification = "请指定要分析的活动，例如双十一或618。"
    elif campaign_match(time_range or "") and comparison == "mom":
        clarification = "请指定活动的对比日期，或询问与去年同一活动的同比。"
    elif campaign_match(time_range or "") and comparison == "yoy" and (
        metrics != ("gmv",) or (dimensions and analysis_type != "contribution")
    ):
        clarification = "当前支持活动销售额同比，请明确要对比的销售额口径。"
    elif "refunded_orders" in metrics and len(metrics) > 1:
        clarification = "退款订单数和已支付订单指标使用不同口径，请分别查询。"
    elif USER_METRICS.intersection(metrics) and (
        set(metrics) - USER_METRICS
        or set(dimensions) - {"vip_level", "area", "order_date", "order_month"}
        or any(item.field not in {"area", "vip_level"} for item in filters)
        or comparison
    ):
        clarification = "用户统计可按会员等级、地区或注册日期查询，请明确用户统计口径。"
    elif not metrics:
        clarification = "请明确要分析的指标，例如销售额、订单量、销量、客单价或复购率。"

    return QuerySpec(
        metrics=_unique(metrics),
        dimensions=_unique(dimensions),
        filters=filters,
        time_range=time_range,
        sort_by=sort_by,
        sort_direction=direction,
        limit=limit,
        minimum_sample_size=minimum_sample_size,
        analysis_type=analysis_type,
        comparison=comparison,
        task_type=capability.task_type,
        capability=capability.capability,
        needs_clarification=bool(clarification),
        clarifying_question=clarification,
    )


def compile_query_spec(spec: QuerySpec, question: str) -> CompiledQuery:
    if spec.needs_clarification:
        return CompiledQuery(sql="", summary=spec.clarifying_question)
    if not spec.metrics:
        return CompiledQuery(sql="", summary="没有解析到可执行指标。")
    if spec.capability == "inventory_forecast":
        return _compile_inventory_forecast_query(spec)
    if spec.analysis_type == "contribution" and spec.comparison and "gmv" in spec.metrics and spec.dimensions:
        return _compile_gmv_contribution_query(spec, question)
    if USER_METRICS.intersection(spec.metrics):
        return _compile_user_query(spec)
    if "repurchase_rate" in spec.metrics:
        return _compile_repurchase_query(spec)
    return _compile_additive_query(spec)


def validate_sql_semantics(spec: QuerySpec, sql: str) -> SemanticValidation:
    if spec.needs_clarification:
        return SemanticValidation(ok=True)
    normalized = " ".join(sql.lower().split())
    errors: list[str] = []
    if not normalized:
        return SemanticValidation(ok=False, errors=("候选 SQL 为空",))
    user_query = bool(USER_METRICS.intersection(spec.metrics))
    status = "refunded" if "refunded_orders" in spec.metrics else "paid"
    if not user_query and not re.search(rf"\bstatus\s*=\s*'{status}'", normalized):
        errors.append(f"查询必须过滤 {status} 订单")

    if spec.capability == "inventory_forecast":
        required = (
            "product_name",
            "quantity",
            "current_stock",
            "forecast_units",
            "safety_stock_units",
            "target_stock",
            "recommended_restock",
        )
        missing = [column for column in required if column not in normalized]
        if missing:
            errors.append(f"库存规划查询缺少字段：{', '.join(missing)}")

    for metric in spec.metrics:
        if metric == "gmv" and not ("sum(" in normalized and ".amount" in normalized and "gmv" in normalized):
            errors.append("销售额必须由 amount 求和并输出 gmv")
        elif metric == "orders" and not ("count(distinct" in normalized and "order_id" in normalized and "orders" in normalized):
            errors.append("订单量必须去重统计 order_id 并输出 orders")
        elif metric == "refunded_orders" and not ("count(distinct" in normalized and "order_id" in normalized and "refunded_orders" in normalized):
            errors.append("退款订单数必须去重统计 order_id 并输出 refunded_orders")
        elif metric in USER_METRICS and not all(token in normalized for token in ("dim_user", "count(distinct", "user_id", metric)):
            errors.append(f"{SUPPORTED_METRICS[metric]}必须去重统计 dim_user.user_id 并输出 {metric}")
        elif metric == "units" and not ("sum(" in normalized and "quantity" in normalized and "units" in normalized):
            errors.append("销量必须由 quantity 求和并输出 units")
        elif metric == "avg_order_value" and not ("amount" in normalized and "order_id" in normalized and "avg_order_value" in normalized):
            errors.append("客单价必须使用销售额除以去重订单量并输出 avg_order_value")
        elif metric == "repurchase_rate":
            required = ("user_id", "order_id", ">= 2", "repurchase_rate_pct", "repeat_users")
            if not all(token in normalized for token in required):
                errors.append("复购率必须先按用户统计 paid 订单数，再计算 paid_orders >= 2 的用户占比")

    for dimension in spec.dimensions:
        column = DIMENSION_RESULT_COLUMNS.get(dimension)
        if column and column not in normalized:
            errors.append(f"查询缺少{SUPPORTED_DIMENSIONS[dimension]}维度 {column}")

    if spec.time_range and ("register_date" if user_query else "order_date") not in normalized:
        errors.append(f"查询遗漏时间范围：{spec.time_range}")
    dimension_filters = tuple(item for item in spec.filters if item.field in DIMENSION_FILTER_COLUMNS)
    if dimension_filters:
        equalities = dimension_filter_equalities(sql)
        for item in dimension_filters:
            column = DIMENSION_FILTER_COLUMNS[item.field].split(".")[-1]
            if (column, item.value) not in equalities:
                errors.append(f"查询遗漏{DIMENSION_FILTER_LABELS[item.field]}筛选：{item.value}")

    if spec.sort_by and spec.dimensions:
        result_column = "gmv_change" if spec.analysis_type == "contribution" else METRIC_RESULT_COLUMNS.get(spec.sort_by, spec.sort_by)
        order_pattern = rf"order\s+by\s+(?:\w+\.)?{re.escape(result_column)}\s+{spec.sort_direction}"
        if not re.search(order_pattern, normalized):
            errors.append(f"排名必须按 {result_column} {spec.sort_direction.upper()} 排序")

    return SemanticValidation(ok=not errors, errors=tuple(errors))


def extract_area_filter(question: str) -> str | None:
    return next((area for area in _AREA_NAMES if area in question), None)


def _compile_inventory_forecast_query(spec: QuerySpec) -> CompiledQuery:
    training_days = spec.training_window_days or 90
    safety_days = spec.safety_stock_days or 7
    forecast_start_sql, forecast_end_sql = _forecast_period_expressions(spec.forecast_horizon)
    horizon_label = _forecast_horizon_label(spec.forecast_horizon)
    sql = f"""
WITH bounds AS (
  SELECT max(order_date)::date AS end_date,
         {forecast_start_sql} AS forecast_start,
         {forecast_end_sql} AS forecast_end
  FROM fact_orders
), product_days AS (
  SELECT p.product_id,
         p.product_name,
         p.stock AS current_stock,
         d.day::date AS sales_date,
         (d.day::date - (b.end_date - INTERVAL '{training_days - 1} days')::date) AS day_index,
         COALESCE(SUM(o.quantity), 0)::numeric AS daily_units
  FROM dim_product p
  CROSS JOIN bounds b
  CROSS JOIN LATERAL generate_series(
    b.end_date - INTERVAL '{training_days - 1} days',
    b.end_date,
    INTERVAL '1 day'
  ) AS d(day)
  LEFT JOIN fact_orders o
    ON o.product_id = p.product_id
   AND o.order_date = d.day::date
   AND o.status = 'paid'
  WHERE p.product_status = 'active'
  GROUP BY p.product_id, p.product_name, p.stock, d.day, b.end_date
), trend_stats AS (
  SELECT product_id,
         product_name,
         current_stock,
         ROUND(AVG(daily_units), 4) AS avg_daily_units,
         ROUND(COALESCE(regr_slope(daily_units, day_index), 0)::numeric, 6) AS trend_units_per_day
  FROM product_days
  GROUP BY product_id, product_name, current_stock
), forecast AS (
  SELECT s.product_id,
         s.product_name,
         s.current_stock,
         s.avg_daily_units,
         s.trend_units_per_day,
         b.forecast_start,
         b.forecast_end,
         COUNT(f.day)::int AS forecast_horizon_days,
         CEIL(SUM(GREATEST(
           0,
           s.avg_daily_units + s.trend_units_per_day * (f.day::date - b.end_date)
         )))::int AS forecast_units
  FROM trend_stats s
  CROSS JOIN bounds b
  CROSS JOIN LATERAL generate_series(b.forecast_start, b.forecast_end, INTERVAL '1 day') AS f(day)
  GROUP BY s.product_id, s.product_name, s.current_stock, s.avg_daily_units,
           s.trend_units_per_day, b.forecast_start, b.forecast_end
), inventory_plan AS (
  SELECT *,
         ((forecast_units * {safety_days} + forecast_horizon_days - 1) / NULLIF(forecast_horizon_days, 0))::int AS safety_stock_units
  FROM forecast
), recommendations AS (
  SELECT product_name,
         forecast_start,
         forecast_end,
         current_stock,
         avg_daily_units,
         trend_units_per_day,
         forecast_units,
         safety_stock_units,
         forecast_units + safety_stock_units AS target_stock,
         GREATEST(forecast_units + safety_stock_units - current_stock, 0)::int AS recommended_restock
  FROM inventory_plan
)
SELECT product_name,
       forecast_start,
       forecast_end,
       current_stock,
       avg_daily_units,
       trend_units_per_day,
       forecast_units,
       safety_stock_units,
       target_stock,
       recommended_restock
FROM recommendations
WHERE recommended_restock > 0
ORDER BY recommended_restock DESC, forecast_units DESC, product_name
""".strip()
    return CompiledQuery(
        sql=sql,
        summary=(
            f"使用最近 {training_days} 天逐日 paid 销量趋势预测{horizon_label}各商品需求，"
            f"并按 {safety_days} 天安全库存减去当前库存计算建议补货量，仅列出建议补货量大于 0 的商品。"
        ),
        chart_type="bar",
        chart_x="product_name",
        chart_y="recommended_restock",
    )


def _forecast_period_expressions(horizon: str | None) -> tuple[str, str]:
    calendar_periods = {
        "next_calendar_week": (
            "(date_trunc('week', max(order_date)) + INTERVAL '1 week')::date",
            "(date_trunc('week', max(order_date)) + INTERVAL '2 weeks' - INTERVAL '1 day')::date",
        ),
        "next_calendar_month": (
            "(date_trunc('month', max(order_date)) + INTERVAL '1 month')::date",
            "(date_trunc('month', max(order_date)) + INTERVAL '2 months' - INTERVAL '1 day')::date",
        ),
        "next_calendar_quarter": (
            "(date_trunc('quarter', max(order_date)) + INTERVAL '3 months')::date",
            "(date_trunc('quarter', max(order_date)) + INTERVAL '6 months' - INTERVAL '1 day')::date",
        ),
        "next_calendar_year": (
            "(date_trunc('year', max(order_date)) + INTERVAL '1 year')::date",
            "(date_trunc('year', max(order_date)) + INTERVAL '2 years' - INTERVAL '1 day')::date",
        ),
    }
    if horizon in calendar_periods:
        return calendar_periods[horizon]
    matched = re.fullmatch(r"next_(\d+)_days", horizon or "")
    days = max(1, min(int(matched.group(1)), 366)) if matched else 30
    return (
        "(max(order_date) + INTERVAL '1 day')::date",
        f"(max(order_date) + INTERVAL '{days} days')::date",
    )


def _forecast_horizon_label(horizon: str | None) -> str:
    labels = {
        "next_calendar_week": "下个自然周",
        "next_calendar_month": "下个自然月",
        "next_calendar_quarter": "下个自然季度",
        "next_calendar_year": "下个自然年",
    }
    if horizon in labels:
        return labels[horizon]
    matched = re.fullmatch(r"next_(\d+)_days", horizon or "")
    return f"未来 {matched.group(1)} 天" if matched else "未来 30 天"


def _compile_user_query(spec: QuerySpec) -> CompiledQuery:
    columns = {
        "vip_level": "u.vip_level",
        "area": "r.area",
        "order_date": "u.register_date",
        "order_month": "date_trunc('month', u.register_date)::date",
    }
    groups = [columns[dimension] for dimension in spec.dimensions]
    selects = [f"{columns[dimension]} AS {DIMENSION_RESULT_COLUMNS[dimension]}" for dimension in spec.dimensions]
    selects.extend(f"COUNT(DISTINCT u.user_id) AS {metric}" for metric in spec.metrics)
    bounds, time_filters = _time_fragments(spec.time_range, date_column="u.register_date")
    joins = _merge_joins(
        "CROSS JOIN bounds" if bounds else "",
        "JOIN dim_region r ON r.region_id = u.region_id"
        if "area" in spec.dimensions or any(item.field == "area" for item in spec.filters) else "",
    )
    where_parts = [*time_filters, *_filter_fragments(spec.filters)]
    where = f"\nWHERE {' AND '.join(where_parts)}" if where_parts else ""
    group_by = f"\nGROUP BY {', '.join(groups)}" if groups else ""
    order_by = _order_by_fragment(spec)
    limit = f"\nLIMIT {spec.limit}" if spec.limit and groups else ""
    coverage = _period_coverage_clause(spec.time_range, users=True)
    sql = f"{bounds}SELECT {', '.join(selects)}\nFROM dim_user u\n{joins}{where}{group_by}{coverage}{order_by}{limit}".strip()
    chart_type = "table"
    if spec.dimensions:
        chart_type = "line" if spec.dimensions in {("order_month",), ("order_date",)} else "bar"
    return CompiledQuery(
        sql=sql,
        summary=f"按用户注册记录统计{'、'.join(SUPPORTED_METRICS[metric] for metric in spec.metrics)}。",
        chart_type=chart_type,
        chart_x=DIMENSION_RESULT_COLUMNS[spec.dimensions[0]] if spec.dimensions else None,
        chart_y=spec.metrics[0],
    )


def _compile_additive_query(spec: QuerySpec) -> CompiledQuery:
    dimensions = _dimension_fragments(spec.dimensions)
    metrics = [_metric_expression(metric) for metric in spec.metrics]
    select_parts = [*dimensions.selects, *metrics]
    bounds, time_filter = _time_fragments(spec.time_range)
    joins = _merge_joins("CROSS JOIN bounds" if bounds else "", dimensions.joins, _filter_joins(spec.filters))
    status = "refunded" if "refunded_orders" in spec.metrics else "paid"
    where_parts = [f"o.status = '{status}'", *time_filter, *_filter_fragments(spec.filters)]
    group_by = f"\nGROUP BY {', '.join(dimensions.groups)}" if dimensions.groups else ""
    coverage = _period_coverage_clause(spec.time_range)
    order_by = _order_by_fragment(spec)
    limit = f"\nLIMIT {spec.limit}" if spec.limit and dimensions.groups else ""
    sql = f"""
{bounds}SELECT {', '.join(select_parts)}
FROM fact_orders o
{joins}
WHERE {' AND '.join(where_parts)}{group_by}{coverage}{order_by}{limit}
""".strip()

    dimension_label = "、".join(SUPPORTED_DIMENSIONS[item] for item in spec.dimensions) or "整体"
    metric_label = "、".join(SUPPORTED_METRICS[item] for item in spec.metrics)
    period_label = spec.time_range or "全部可用周期"
    chart_x = DIMENSION_RESULT_COLUMNS.get(spec.dimensions[0]) if spec.dimensions else None
    chart_y = METRIC_RESULT_COLUMNS.get(spec.metrics[0])
    chart_type = "line" if spec.dimensions in {("order_date",), ("order_month",)} else "bar" if spec.dimensions else "table"
    return CompiledQuery(
        sql=sql,
        summary=f"按{dimension_label}统计{period_label}的 paid {metric_label}。",
        chart_type=chart_type,
        chart_x=chart_x,
        chart_y=chart_y,
    )


def _compile_gmv_contribution_query(spec: QuerySpec, question: str) -> CompiledQuery:
    dimensions = _dimension_fragments(spec.dimensions)
    time_intent = parse_time_intent(spec.time_range or question)
    time_intent = replace(time_intent, comparison=spec.comparison)
    current_predicate, comparison_predicate = build_period_predicates(time_intent)
    joins = _merge_joins(dimensions.joins, _filter_joins(spec.filters))
    where_parts = ["o.status = 'paid'", *_filter_fragments(spec.filters)]
    group_by = ", ".join(dimensions.groups)
    dimension_columns = ", ".join(DIMENSION_RESULT_COLUMNS[item] for item in spec.dimensions)
    limit = f"\nLIMIT {spec.limit}" if spec.limit else ""
    sql = f"""
WITH bounds AS (SELECT max(order_date) AS end_date FROM fact_orders),
contribution_base AS (
  SELECT {', '.join(dimensions.selects)},
         COALESCE(SUM(o.amount) FILTER (WHERE {current_predicate}), 0) AS current_gmv,
         COALESCE(SUM(o.amount) FILTER (WHERE {comparison_predicate}), 0) AS previous_gmv
  FROM fact_orders o
  CROSS JOIN bounds
  {joins}
  WHERE {' AND '.join(where_parts)}
  GROUP BY {group_by}
), contribution_result AS (
  SELECT {dimension_columns}, current_gmv, previous_gmv,
         ROUND(current_gmv - previous_gmv, 2) AS gmv_change,
         ROUND((current_gmv - previous_gmv) / NULLIF(previous_gmv, 0) * 100, 2) AS change_pct
  FROM contribution_base
)
SELECT *,
       ROUND(gmv_change / NULLIF(SUM(gmv_change) OVER (), 0) * 100, 2) AS contribution_pct
FROM contribution_result
ORDER BY gmv_change {spec.sort_direction.upper()}{limit}
""".strip()
    dimension_label = "、".join(SUPPORTED_DIMENSIONS[item] for item in spec.dimensions)
    comparison_label = "同比" if spec.comparison == "yoy" else "环比"
    return CompiledQuery(
        sql=sql,
        summary=f"按{dimension_label}拆解{spec.time_range or time_intent.label} paid GMV 的{comparison_label}增量贡献。",
        chart_type="bar",
        chart_x=DIMENSION_RESULT_COLUMNS.get(spec.dimensions[0]),
        chart_y="gmv_change",
    )


def _compile_repurchase_query(spec: QuerySpec) -> CompiledQuery:
    dimensions = _dimension_fragments(spec.dimensions)
    bounds, time_filter = _time_fragments(spec.time_range)
    joins = _merge_joins("CROSS JOIN bounds" if bounds else "", dimensions.joins, _filter_joins(spec.filters))
    where_parts = ["o.status = 'paid'", *time_filter, *_filter_fragments(spec.filters)]
    inner_dimensions = f"{', '.join(dimensions.selects)}, " if dimensions.selects else ""
    inner_groups = [*dimensions.groups, "o.user_id"]
    outer_dimensions = f"{', '.join(DIMENSION_RESULT_COLUMNS[item] for item in spec.dimensions)}, " if spec.dimensions else ""
    outer_groups = [DIMENSION_RESULT_COLUMNS[item] for item in spec.dimensions]
    extra_metrics = _repurchase_extra_metrics(spec.metrics)
    outer_group_sql = f"\nGROUP BY {', '.join(outer_groups)}" if outer_groups else ""
    having = ""
    if outer_groups and spec.minimum_sample_size:
        having = f"\nHAVING COUNT(*) >= {spec.minimum_sample_size}"
    if not outer_groups:
        having = _period_coverage_clause(spec.time_range)
    order_by = _order_by_fragment(spec)
    limit = f"\nLIMIT {spec.limit}" if spec.limit and outer_groups else ""
    cte_prefix = f"{bounds.rstrip()},\n" if bounds else "WITH "
    sql = f"""
{cte_prefix}user_orders AS (
  SELECT {inner_dimensions}o.user_id,
         COUNT(DISTINCT o.order_id) AS paid_orders,
         SUM(o.amount) AS user_gmv,
         SUM(o.quantity) AS user_units
  FROM fact_orders o
  {joins}
  WHERE {' AND '.join(where_parts)}
  GROUP BY {', '.join(inner_groups)}
)
SELECT {outer_dimensions}COUNT(*) AS paid_users,
       COUNT(*) FILTER (WHERE paid_orders >= 2) AS repeat_users,
       ROUND(COUNT(*) FILTER (WHERE paid_orders >= 2)::numeric / NULLIF(COUNT(*), 0) * 100, 2) AS repurchase_rate_pct{extra_metrics}
FROM user_orders{outer_group_sql}{having}{order_by}{limit}
""".strip()
    dimension_label = "、".join(SUPPORTED_DIMENSIONS[item] for item in spec.dimensions) or "整体"
    period_label = spec.time_range or "全部可用周期"
    chart_x = DIMENSION_RESULT_COLUMNS.get(spec.dimensions[0]) if spec.dimensions else None
    return CompiledQuery(
        sql=sql,
        summary=f"按{dimension_label}统计{period_label}内同一用户 paid 订单数不少于 2 次的复购率。",
        chart_type="line" if spec.dimensions == ("order_month",) else "bar" if spec.dimensions else "table",
        chart_x=chart_x,
        chart_y="repurchase_rate_pct" if spec.dimensions else None,
    )


@dataclass(frozen=True)
class _DimensionFragments:
    selects: tuple[str, ...]
    groups: tuple[str, ...]
    joins: str


def _dimension_fragments(dimensions: Iterable[str]) -> _DimensionFragments:
    selects: list[str] = []
    groups: list[str] = []
    joins: list[str] = []
    for dimension in dimensions:
        if dimension == "product":
            selects.append("p.product_name")
            groups.append("p.product_name")
            joins.append("JOIN dim_product p ON p.product_id = o.product_id")
        elif dimension == "category":
            selects.append("p.category")
            groups.append("p.category")
            joins.append("JOIN dim_product p ON p.product_id = o.product_id")
        elif dimension == "channel":
            selects.append("o.channel")
            groups.append("o.channel")
        elif dimension == "area":
            selects.append("r.area")
            groups.append("r.area")
            joins.append("JOIN dim_region r ON r.region_id = o.region_id")
        elif dimension == "vip_level":
            selects.append("u.vip_level")
            groups.append("u.vip_level")
            joins.append("JOIN dim_user u ON u.user_id = o.user_id")
        elif dimension == "order_date":
            selects.append("o.order_date")
            groups.append("o.order_date")
        elif dimension == "order_month":
            month = "date_trunc('month', o.order_date)::date"
            selects.append(f"{month} AS order_month")
            groups.append(month)
    unique_joins = tuple(dict.fromkeys(joins))
    return _DimensionFragments(tuple(selects), tuple(groups), "\n".join(unique_joins))


def _metric_expression(metric: str) -> str:
    expressions = {
        "gmv": "SUM(o.amount) AS gmv",
        "orders": "COUNT(DISTINCT o.order_id) AS orders",
        "refunded_orders": "COUNT(DISTINCT o.order_id) AS refunded_orders",
        "units": "SUM(o.quantity) AS units",
        "avg_order_value": "ROUND(SUM(o.amount) / NULLIF(COUNT(DISTINCT o.order_id), 0), 2) AS avg_order_value",
    }
    return expressions[metric]


def _repurchase_extra_metrics(metrics: Iterable[str]) -> str:
    extras: list[str] = []
    for metric in metrics:
        if metric == "gmv":
            extras.append("SUM(user_gmv) AS gmv")
        elif metric == "orders":
            extras.append("SUM(paid_orders) AS orders")
        elif metric == "units":
            extras.append("SUM(user_units) AS units")
        elif metric == "avg_order_value":
            extras.append("ROUND(SUM(user_gmv) / NULLIF(SUM(paid_orders), 0), 2) AS avg_order_value")
    return "" if not extras else ",\n       " + ",\n       ".join(extras)


def _time_fragments(
    time_range: str | None, *, date_column: str = "o.order_date"
) -> tuple[str, list[str]]:
    if not time_range:
        return "", []
    intent = parse_time_intent(time_range)
    predicate, _ = build_period_predicates(intent, date_column)
    return "WITH bounds AS (SELECT max(order_date) AS end_date FROM fact_orders)\n", [predicate]


def _period_coverage_clause(time_range: str | None, *, users: bool = False) -> str:
    intent = parse_time_intent(time_range or "")
    if not (intent.campaign_day or intent.calendar_year):
        return ""
    table, column = ("dim_user", "register_date") if users else ("fact_orders", "order_date")
    predicate, _ = build_period_predicates(intent, f"observed.{column}")
    return f"\nHAVING EXISTS (SELECT 1 FROM {table} observed CROSS JOIN bounds WHERE {predicate})"


def _filter_fragments(filters: Iterable[QueryFilter]) -> list[str]:
    return [
        dimension_filter_sql(item.field, item.value)
        for item in filters
        if item.field in DIMENSION_FILTER_COLUMNS and item.operator == "="
    ]


def _filter_joins(filters: Iterable[QueryFilter]) -> str:
    return "\n".join(dict.fromkeys(
        DIMENSION_FILTER_JOINS[item.field]
        for item in filters
        if item.field in DIMENSION_FILTER_JOINS and item.operator == "="
    ))


def _merge_joins(*groups: str) -> str:
    lines = []
    for group in groups:
        lines.extend(line for line in group.splitlines() if line.strip())
    return "\n".join(dict.fromkeys(lines))


def _order_by_fragment(spec: QuerySpec) -> str:
    if not spec.sort_by or not spec.dimensions:
        return ""
    column = METRIC_RESULT_COLUMNS.get(spec.sort_by, spec.sort_by)
    secondary = ", paid_users DESC" if spec.sort_by == "repurchase_rate" else ""
    return f"\nORDER BY {column} {spec.sort_direction.upper()}{secondary}"


def _detect_metrics(question: str) -> tuple[tuple[str, ...], str | None]:
    text = question.lower().replace(" ", "")
    if "滞销" in text:
        return ("units",), None
    if "转化率" in text or "转化" in text:
        return (), "conversion_rate"
    if "毛利" in text or "利润" in text:
        return (), "profit"
    refund_count = bool(re.search(r"退款(?:的)?订单|订单退款|退款(?:单量|单数|有多少|多少单)", text))
    registrations = bool(re.search(r"新增(?:的)?(?:注册)?用户|新注册(?:的)?用户|注册用户|新客(?:数|量)", text))
    distribution = ("会员等级" in text or "vip等级" in text) and "分布" in text
    ordered = (
        ("repurchase_rate", ("复购率", "复购")),
        ("avg_order_value", ("客单价", "aov")),
        ("units", ("销量", "件数")),
        ("orders", ("订单量", "订单数", "单量")),
        ("gmv", ("销售额", "gmv", "成交额", "金额")),
    )
    metrics = tuple(metric for metric, aliases in ordered if any(alias in text for alias in aliases))
    if re.search(r"\b(?:revenue|gross merchandise value)\b", question, re.IGNORECASE) and "gmv" not in metrics:
        metrics = (*metrics, "gmv")
    if refund_count:
        metrics = ("refunded_orders", *(metric for metric in metrics if metric != "orders"))
    if registrations:
        metrics = ("registered_users", *metrics)
    elif distribution and not metrics:
        metrics = ("users",)
    if not metrics and any(marker in text for marker in _SELL_THROUGH_RANKING):
        metrics = ("units",)
    return metrics, None


def _detect_dimensions(question: str) -> tuple[str, ...]:
    text = question.lower().replace(" ", "")
    dimensions: list[str] = []
    if any(token in text for token in ("类目", "品类")):
        dimensions.append("category")
    elif any(token in text for token in ("商品", "货品", "sku")):
        dimensions.append("product")
    if "渠道" in text:
        dimensions.append("channel")
    if any(token in text for token in ("哪个地区", "哪些地区", "哪个区域", "哪些区域", "哪个大区", "各地区", "各区域", "各大区")):
        dimensions.append("area")
    if "会员等级" in text or "vip等级" in text:
        dimensions.append("vip_level")
    if any(token in text for token in ("各月", "每月", "每个月", "逐月", "按月", "分月", "月度")):
        dimensions.append("order_month")
    elif any(token in text for token in ("趋势", "每日", "每天", "按天")):
        dimensions.append("order_date")
    dimensions.extend(
        _ENGLISH_GROUPS[group] for group in _english_group_labels(question) if group in _ENGLISH_GROUPS
    )
    return _unique(dimensions)


def _english_group_labels(question: str) -> tuple[str, ...]:
    return tuple(
        " ".join(group.split())
        for groups in _ENGLISH_GROUP.findall(question.lower())
        for group in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", groups)
    )


def _payload_filters(value: Any) -> tuple[QueryFilter, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    filters = []
    for item in value:
        if isinstance(item, dict):
            parsed = QueryFilter.from_dict(item)
            if parsed:
                if parsed.field in DIMENSION_FILTER_COLUMNS:
                    if parsed.operator != "=":
                        continue
                    parsed = replace(parsed, value=dimension_filter_value(parsed.field, parsed.value))
                filters.append(parsed)
    return tuple(filters)


def _merge_filters(*groups: tuple[QueryFilter, ...]) -> tuple[QueryFilter, ...]:
    merged: dict[str, QueryFilter] = {}
    for group in groups:
        for item in group:
            merged[item.field] = item
    return tuple(merged.values())


def _normalize_many(value: Any, aliases: dict[str, str]) -> tuple[str, ...]:
    # QuerySpec.to_dict() preserves tuples, while JSON model payloads use lists.
    # Accept both so a spec can round-trip through the in-memory conversation store.
    if not isinstance(value, (list, tuple)):
        return ()
    normalized = []
    for item in value:
        key = str(item).strip().lower()
        if key in aliases:
            normalized.append(aliases[key])
    return _unique(normalized)


def _normalize_metric(value: Any) -> str | None:
    if value is None:
        return None
    return _METRIC_ALIASES.get(str(value).strip().lower())


def _detect_analysis_type(question: str) -> str | None:
    text = question.lower().replace(" ", "")
    if any(token in text for token in ("贡献来源", "主要贡献", "增长贡献", "拉动", "驱动")):
        return "contribution"
    return None


def _normalize_analysis_type(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"aggregate", "contribution"} else None


def _detect_comparison(question: str) -> str | None:
    text = question.lower().replace(" ", "")
    if "同比" in text or "去年同期" in text:
        return "yoy"
    if any(token in text for token in ("环比", "前一周期", "上一周期", "上期")):
        return "mom"
    return None


def _normalize_comparison(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"mom", "yoy"} else None


def _can_default_to_gmv(question: str, dimensions: tuple[str, ...]) -> bool:
    text = question.lower()
    ranking_default = bool(dimensions) and any(token in text for token in ("top", "最高", "排名", "排行", "哪些"))
    trend_default = bool({"order_date", "order_month"}.intersection(dimensions)) and any(
        token in text for token in ("销售", "成交", "流水", "趋势")
    )
    return ranking_default or trend_default


def _is_ranking_question(question: str) -> bool:
    text = question.lower().replace(" ", "")
    return any(marker in text for marker in _RANKING_MARKERS)


def _extract_limit(question: str) -> int | None:
    match = re.search(r"(?:top|前)\s*(\d+)", question.lower())
    return max(1, min(int(match.group(1)), 200)) if match else None


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
