from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from ecommerce_agent.time_intelligence import parse_time_intent


SUPPORTED_METRICS = {
    "gmv": "销售额/GMV",
    "orders": "订单量",
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
}

METRIC_RESULT_COLUMNS = {
    "gmv": "gmv",
    "orders": "orders",
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
}

_METRIC_ALIASES = {
    "gmv": "gmv",
    "销售额": "gmv",
    "成交额": "gmv",
    "金额": "gmv",
    "orders": "orders",
    "order_count": "orders",
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
}

_AREA_NAMES = ("华东", "华南", "华北", "西南", "华中", "西北", "东北")
_TIME_MARKERS = ("近", "最近", "上周", "上星期", "本周", "本月", "这个月", "上月", "上个月", "今年", "年度")
_RANKING_MARKERS = ("最高", "最低", "top", "前", "排名", "排行", "哪些", "什么", "更高")


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


def build_query_spec(question: str, payload: dict[str, Any] | None = None) -> QuerySpec:
    """Ground an LLM plan against explicit metric/dimension words in the question.

    The model is free to infer omitted slots, while explicit user constraints always
    win.  This prevents a fluent but semantically different plan from silently
    replacing "复购率" with GMV or dropping a requested dimension.
    """

    payload = payload or {}
    supplied_metrics = _normalize_many(payload.get("metrics"), _METRIC_ALIASES)
    supplied_dimensions = _normalize_many(payload.get("dimensions"), _DIMENSION_ALIASES)
    explicit_metrics, unsupported_metric = _detect_metrics(question)
    explicit_dimensions = _detect_dimensions(question)

    metrics = explicit_metrics or supplied_metrics
    dimensions = explicit_dimensions or supplied_dimensions
    if not metrics and not unsupported_metric and _can_default_to_gmv(question, dimensions):
        metrics = ("gmv",)

    filters = _payload_filters(payload.get("filters"))
    area_filter = extract_area_filter(question)
    if area_filter and not any(item.field == "area" for item in filters):
        filters = (*filters, QueryFilter(field="area", operator="=", value=area_filter))
    if not any(item.field == "status" for item in filters):
        filters = (*filters, QueryFilter(field="status", operator="=", value="paid"))

    time_range = _explicit_time_range(question) or _optional_string(payload.get("time_range"))
    if "order_date" in dimensions and not time_range:
        time_range = "近 30 天"

    sort_by = _normalize_metric(payload.get("sort_by"))
    if metrics and (_is_ranking_question(question) or dimensions):
        sort_by = metrics[0]
    direction = "asc" if any(token in question.lower() for token in ("最低", "最少", "倒数")) else "desc"
    if str(payload.get("sort_direction") or "").lower() in {"asc", "desc"} and not _is_ranking_question(question):
        direction = str(payload["sort_direction"]).lower()

    limit = _extract_limit(question)
    if limit is None:
        candidate_limit = payload.get("limit")
        if isinstance(candidate_limit, int):
            limit = max(1, min(candidate_limit, 200))
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
        needs_clarification=bool(clarification),
        clarifying_question=clarification,
    )


def compile_query_spec(spec: QuerySpec, question: str) -> CompiledQuery:
    if spec.needs_clarification:
        return CompiledQuery(sql="", summary=spec.clarifying_question)
    if not spec.metrics:
        return CompiledQuery(sql="", summary="没有解析到可执行指标。")
    if "repurchase_rate" in spec.metrics:
        return _compile_repurchase_query(spec, question)
    return _compile_additive_query(spec, question)


def validate_sql_semantics(spec: QuerySpec, sql: str) -> SemanticValidation:
    if spec.needs_clarification:
        return SemanticValidation(ok=True)
    normalized = " ".join(sql.lower().split())
    errors: list[str] = []
    if not normalized:
        return SemanticValidation(ok=False, errors=("候选 SQL 为空",))
    if not re.search(r"\bstatus\s*=\s*'paid'", normalized):
        errors.append("查询必须过滤 paid 订单")

    for metric in spec.metrics:
        if metric == "gmv" and not ("sum(" in normalized and ".amount" in normalized and "gmv" in normalized):
            errors.append("销售额必须由 amount 求和并输出 gmv")
        elif metric == "orders" and not ("count(distinct" in normalized and "order_id" in normalized and "orders" in normalized):
            errors.append("订单量必须去重统计 order_id 并输出 orders")
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

    if spec.time_range and "order_date" not in normalized:
        errors.append(f"查询遗漏时间范围：{spec.time_range}")
    for item in spec.filters:
        if item.field == "area" and item.value not in sql:
            errors.append(f"查询遗漏地区筛选：{item.value}")

    if spec.sort_by and spec.dimensions:
        result_column = METRIC_RESULT_COLUMNS.get(spec.sort_by, spec.sort_by)
        order_pattern = rf"order\s+by\s+(?:\w+\.)?{re.escape(result_column)}\s+{spec.sort_direction}"
        if not re.search(order_pattern, normalized):
            errors.append(f"排名必须按 {result_column} {spec.sort_direction.upper()} 排序")

    return SemanticValidation(ok=not errors, errors=tuple(errors))


def extract_area_filter(question: str) -> str | None:
    return next((area for area in _AREA_NAMES if area in question), None)


def _compile_additive_query(spec: QuerySpec, question: str) -> CompiledQuery:
    dimensions = _dimension_fragments(spec.dimensions)
    metrics = [_metric_expression(metric) for metric in spec.metrics]
    select_parts = [*dimensions.selects, *metrics]
    bounds, time_filter = _time_fragments(question, spec.time_range)
    joins = _merge_joins("CROSS JOIN bounds" if bounds else "", dimensions.joins, _filter_joins(spec.filters))
    where_parts = ["o.status = 'paid'", *time_filter, *_filter_fragments(spec.filters)]
    group_by = f"\nGROUP BY {', '.join(dimensions.groups)}" if dimensions.groups else ""
    order_by = _order_by_fragment(spec)
    limit = f"\nLIMIT {spec.limit}" if spec.limit and dimensions.groups else ""
    sql = f"""
{bounds}SELECT {', '.join(select_parts)}
FROM fact_orders o
{joins}
WHERE {' AND '.join(where_parts)}{group_by}{order_by}{limit}
""".strip()

    dimension_label = "、".join(SUPPORTED_DIMENSIONS[item] for item in spec.dimensions) or "整体"
    metric_label = "、".join(SUPPORTED_METRICS[item] for item in spec.metrics)
    period_label = spec.time_range or "全部可用周期"
    chart_x = DIMENSION_RESULT_COLUMNS.get(spec.dimensions[0]) if spec.dimensions else None
    chart_y = METRIC_RESULT_COLUMNS.get(spec.metrics[0])
    chart_type = "line" if spec.dimensions == ("order_date",) else "bar" if spec.dimensions else "table"
    return CompiledQuery(
        sql=sql,
        summary=f"按{dimension_label}统计{period_label}的 paid {metric_label}。",
        chart_type=chart_type,
        chart_x=chart_x,
        chart_y=chart_y,
    )


def _compile_repurchase_query(spec: QuerySpec, question: str) -> CompiledQuery:
    dimensions = _dimension_fragments(spec.dimensions)
    bounds, time_filter = _time_fragments(question, spec.time_range)
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
        chart_type="bar" if spec.dimensions else "table",
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
    unique_joins = tuple(dict.fromkeys(joins))
    return _DimensionFragments(tuple(selects), tuple(groups), "\n".join(unique_joins))


def _metric_expression(metric: str) -> str:
    expressions = {
        "gmv": "SUM(o.amount) AS gmv",
        "orders": "COUNT(DISTINCT o.order_id) AS orders",
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


def _time_fragments(question: str, time_range: str | None) -> tuple[str, list[str]]:
    if not time_range:
        return "", []
    intent = parse_time_intent(question if _has_explicit_time(question) else time_range)
    anchor = "end_date"
    if intent.period_offset:
        unit = {"month": "month", "week": "week", "year": "year"}[intent.calendar_period or "month"]
        anchor = f"end_date - INTERVAL '{abs(intent.period_offset)} {unit}'"
    if intent.calendar_period:
        unit = intent.calendar_period
        predicate = f"o.order_date >= date_trunc('{unit}', {anchor})::date AND o.order_date < date_trunc('{unit}', {anchor}) + INTERVAL '1 {unit}'"
    else:
        predicate = f"o.order_date BETWEEN end_date - INTERVAL '{intent.window_days - 1} days' AND end_date"
    return "WITH bounds AS (SELECT max(order_date) AS end_date FROM fact_orders)\n", [predicate]


def _filter_fragments(filters: Iterable[QueryFilter]) -> list[str]:
    fragments: list[str] = []
    for item in filters:
        if item.field == "area":
            fragments.append(f"r.area = '{item.value}'")
    return fragments


def _filter_joins(filters: Iterable[QueryFilter]) -> str:
    joins = []
    if any(item.field == "area" for item in filters):
        joins.append("JOIN dim_region r ON r.region_id = o.region_id")
    return "\n".join(joins)


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
    if "转化率" in text or "转化" in text:
        return (), "conversion_rate"
    if "毛利" in text or "利润" in text:
        return (), "profit"
    ordered = (
        ("repurchase_rate", ("复购率", "复购")),
        ("avg_order_value", ("客单价", "aov")),
        ("units", ("销量", "件数")),
        ("orders", ("订单量", "订单数", "单量")),
        ("gmv", ("销售额", "gmv", "成交额", "金额")),
    )
    metrics = tuple(metric for metric, aliases in ordered if any(alias in text for alias in aliases))
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
    if any(token in text for token in ("趋势", "每日", "每天", "按天")):
        dimensions.append("order_date")
    return _unique(dimensions)


def _payload_filters(value: Any) -> tuple[QueryFilter, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    filters = []
    for item in value:
        if isinstance(item, dict):
            parsed = QueryFilter.from_dict(item)
            if parsed:
                filters.append(parsed)
    return tuple(filters)


def _normalize_many(value: Any, aliases: dict[str, str]) -> tuple[str, ...]:
    if not isinstance(value, list):
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


def _can_default_to_gmv(question: str, dimensions: tuple[str, ...]) -> bool:
    text = question.lower()
    ranking_default = bool(dimensions) and any(token in text for token in ("top", "最高", "排名", "排行", "哪些"))
    trend_default = "order_date" in dimensions and any(token in text for token in ("销售", "成交", "流水", "趋势"))
    return ranking_default or trend_default


def _explicit_time_range(question: str) -> str | None:
    if not _has_explicit_time(question):
        return None
    return parse_time_intent(question).label


def _has_explicit_time(question: str) -> bool:
    text = question.lower().replace(" ", "")
    return bool(re.search(r"(?:近|最近)\d+(?:天|日)", text)) or any(marker in text for marker in _TIME_MARKERS[2:])


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
