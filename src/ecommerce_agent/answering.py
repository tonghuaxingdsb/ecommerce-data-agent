from __future__ import annotations

from typing import Any

from ecommerce_agent.semantic import DIMENSION_RESULT_COLUMNS, METRIC_RESULT_COLUMNS, SUPPORTED_DIMENSIONS, SUPPORTED_METRICS, build_query_spec
from ecommerce_agent.time_intelligence import parse_time_intent


def build_fallback_analysis(question: str, intent: str, rows: list[dict[str, Any]], language: str) -> str:
    if language == "zh":
        return build_chinese_analysis(question, intent, rows)
    return build_english_analysis(question, intent, rows)


def is_usable_analysis(text: str, language: str) -> bool:
    stripped = text.strip()
    minimum_length = 16 if language == "zh" else 30
    if len(stripped) < minimum_length:
        return False
    if stripped.endswith(("（", "(", "[", "【", "：", ":", "，", ",", "；", ";", "、")):
        return False
    for opening, closing in (("（", "）"), ("(", ")"), ("[", "]"), ("【", "】")):
        if stripped.count(opening) != stripped.count(closing):
            return False
    incomplete_endings = ("例如", "包括", "分别为", "如下", "其中", "以及", "因为", "因此为")
    return not stripped.endswith(incomplete_endings)


def build_chinese_analysis(question: str, intent: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        if intent == "inventory_planning":
            return "按当前趋势预测和安全库存假设，没有商品需要补货。建议仍结合在途库存、采购提前期和活动计划复核。"
        return "这次查询没有返回数据。建议先放宽时间范围或确认筛选条件是否过窄。"

    first = rows[0]
    spec = build_query_spec(question)
    if "recommended_restock" in first:
        total_restock = sum(number_or_zero(row.get("recommended_restock")) for row in rows)
        top_rows = [row for row in rows if number_or_zero(row.get("recommended_restock")) > 0][:3]
        top_items = "、".join(
            f"{row.get('product_name')} {format_int(row.get('recommended_restock'))} 件" for row in top_rows
        ) or "暂无需要补货的商品"
        return (
            f"按最近 {spec.training_window_days or 90} 天逐日 paid 销量趋势估算，"
            f"{first.get('forecast_start')} 至 {first.get('forecast_end')} 共有 {len(rows)} 个商品需要补货，"
            f"建议补货合计约 {format_int(total_restock)} 件。"
            f"建议补货量最高的商品为：{top_items}。"
            f"该结果按 {spec.safety_stock_days or 7} 天安全库存计算，并假设在途库存和采购提前期为 0；"
            "它是基线计划，不是确定采购指令，实际下单前应补充在途库存、采购提前期和目标服务水平。"
        )
    if "gmv_change" in first:
        dimension = next((item for item in spec.dimensions if DIMENSION_RESULT_COLUMNS.get(item) in first), None)
        dimension_column = DIMENSION_RESULT_COLUMNS.get(dimension or "")
        if dimension and dimension_column:
            change = number_or_zero(first.get("gmv_change"))
            effect = "拉动" if change >= 0 else "拖累"
            top_names = "、".join(str(row.get(dimension_column)) for row in rows[:3])
            return (
                f"{SUPPORTED_DIMENSIONS[dimension]}「{first.get(dimension_column)}」是当前排名第一的 GMV {effect}来源，"
                f"本期销售额 {format_money(first.get('current_gmv'))}，比较期 {format_money(first.get('previous_gmv'))}，"
                f"增量 {format_money(change)}，占整体净增量约 {number_or_zero(first.get('contribution_pct')):.2f}%。"
                f"增量 Top3 为 {top_names}；建议同时关注负贡献项，区分增长来源与下滑拖累。"
            )
    if "order_date" in first and spec.metrics:
        metric = spec.metrics[0]
        metric_column = METRIC_RESULT_COLUMNS[metric]
        total = sum(number_or_zero(row.get(metric_column)) for row in rows)
        peak = max(rows, key=lambda row: number_or_zero(row.get(metric_column)))
        return (
            f"{spec.time_range or '当前周期'}累计{SUPPORTED_METRICS[metric]}为 {format_metric_value(metric, total)}。"
            f"峰值出现在 {peak.get('order_date')}，当日指标值为 {format_metric_value(metric, peak.get(metric_column))}。"
            "建议结合活动日和渠道进一步解释峰值来源。"
        )

    if "repurchase_rate_pct" in first:
        dimension = next((item for item in spec.dimensions if DIMENSION_RESULT_COLUMNS.get(item) in first), None)
        if dimension:
            column = DIMENSION_RESULT_COLUMNS[dimension]
            top = first
            return (
                f"按当前口径，{SUPPORTED_DIMENSIONS[dimension]}「{top.get(column)}」的复购率最高，为 "
                f"{number_or_zero(top.get('repurchase_rate_pct')):.2f}%。购买用户 {format_int(top.get('paid_users'))} 人，"
                f"其中复购用户 {format_int(top.get('repeat_users'))} 人。"
                f"该口径按{SUPPORTED_DIMENSIONS[dimension]}内同一用户至少产生 2 个不同 paid 订单计算，"
                "排名时需要同时关注购买用户数，避免小样本放大。"
            )
        return (
            f"当前口径下付费用户数为 {format_int(first.get('paid_users'))} 人，其中复购用户 "
            f"{format_int(first.get('repeat_users'))} 人，复购率为 {number_or_zero(first.get('repurchase_rate_pct')):.2f}%。"
            "建议继续按商品、会员等级、渠道或地区拆分。"
        )

    if intent == "sales_overview" and "current_gmv" in first:
        time_intent = parse_time_intent(question)
        current_gmv = format_money(first.get("current_gmv"))
        previous_gmv = format_money(first.get("previous_gmv"))
        current_orders = format_int(first.get("current_orders"))
        current_units = format_int(first.get("current_units"))
        mom = first.get("change_pct", first.get("gmv_mom_pct"))
        aov = format_money(first.get("current_avg_order_value"))
        direction = "增长" if number_or_zero(mom) >= 0 else "下降"
        comparison_label = "同比" if time_intent.comparison == "yoy" else "环比"
        comparison_period = "去年同期" if time_intent.comparison == "yoy" else "前一周期"
        return (
            f"{time_intent.label}销售额为 {current_gmv}，{comparison_period}为 {previous_gmv}，{comparison_label}{direction} {abs(number_or_zero(mom)):.2f}%。"
            f"同期订单量为 {current_orders} 单，销量 {current_units} 件，客单价约 {aov}。"
            "整体看，本期销售表现较上一周期更强，可以继续拆到渠道或商品类目看主要贡献来源。"
        )

    primary_metric = spec.metrics[0] if spec.metrics else None
    primary_dimension = spec.dimensions[0] if spec.dimensions else None
    if primary_metric and primary_dimension:
        metric_column = METRIC_RESULT_COLUMNS.get(primary_metric)
        dimension_column = DIMENSION_RESULT_COLUMNS.get(primary_dimension)
        if metric_column in first and dimension_column in first:
            direction = "最高" if spec.sort_direction == "desc" else "最低"
            return (
                f"按{SUPPORTED_METRICS[primary_metric]}看，{SUPPORTED_DIMENSIONS[primary_dimension]}"
                f"「{first.get(dimension_column)}」排名{direction}，指标值为 {format_metric_value(primary_metric, first.get(metric_column))}。"
                f"结果已按 {metric_column} {spec.sort_direction.upper()} 排序，可在明细表继续查看其他{SUPPORTED_DIMENSIONS[primary_dimension]}。"
            )

    if primary_metric and not primary_dimension:
        metric_column = METRIC_RESULT_COLUMNS.get(primary_metric)
        if metric_column in first:
            return (
                f"{spec.time_range or '当前统计周期'}的{SUPPORTED_METRICS[primary_metric]}为 "
                f"{format_metric_value(primary_metric, first.get(metric_column))}。"
                "可以继续按商品、渠道、地区或日期拆分查看贡献。"
            )

    if intent == "product_analysis" and "product_name" in first:
        top = first
        total_gmv = sum(number_or_zero(row.get("gmv")) for row in rows)
        top_share = number_or_zero(top.get("gmv")) / total_gmv * 100 if total_gmv else 0
        top_names = "、".join(str(row.get("product_name")) for row in rows[:3])
        return (
            f"按销售额看，排名第一的是「{top.get('product_name')}」，类目为「{top.get('category')}」，"
            f"销售额 {format_money(top.get('gmv'))}，销量 {format_int(top.get('units'))} 件，订单量 {format_int(top.get('orders'))} 单。"
            f"Top3 商品分别是 {top_names}。在当前返回的商品中，第一名贡献约 {top_share:.2f}% 的销售额，"
            "后续可以进一步看这些商品的渠道来源和库存是否匹配。"
        )

    if intent == "product_analysis" and "category" in first:
        return (
            f"当前销售额最高的类目是「{first.get('category')}」，销售额 {format_money(first.get('gmv'))}，"
            f"销量 {format_int(first.get('units'))} 件，订单量 {format_int(first.get('orders'))} 单。"
            "建议继续对头部类目拆商品和渠道，判断增长是由爆品拉动还是类目整体增长。"
        )

    if intent == "channel_analysis":
        top = first
        return (
            f"销售额最高的渠道是「{top.get('channel')}」，销售额 {format_money(top.get('gmv'))}，"
            f"订单量 {format_int(top.get('orders'))} 单，销量 {format_int(top.get('units'))} 件，"
            f"客单价约 {format_money(top.get('avg_order_value'))}。"
            "建议优先复盘该渠道的投放、人群和商品组合，再对比低销售额渠道的转化与客单价差异。"
        )

    if intent == "user_analysis":
        paid_users = format_int(first.get("paid_users"))
        repeat_users = format_int(first.get("repeat_users"))
        rate = number_or_zero(first.get("repurchase_rate_pct"))
        return (
            f"当前口径下付费用户数为 {paid_users} 人，其中复购用户 {repeat_users} 人，复购率为 {rate:.2f}%。"
            "如果这是核心运营指标，建议继续按会员等级、注册时间和渠道拆分，定位复购表现更好的用户群。"
        )

    return f"已根据问题「{question}」完成查询，共返回 {len(rows)} 行结果。建议查看结果表中的头部项，并继续按时间、地区或渠道做拆分。"


def build_english_analysis(question: str, intent: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "The query returned no rows. Try widening the time range or relaxing the filters."
    return f"The query for '{question}' returned {len(rows)} rows. The top result is {rows[0]}."


def format_money(value: Any) -> str:
    amount = number_or_zero(value)
    if abs(amount) >= 10_000:
        return f"{amount / 10_000:.2f} 万元"
    return f"{amount:.2f} 元"


def format_int(value: Any) -> str:
    return f"{int(number_or_zero(value)):,}"


def format_metric_value(metric: str, value: Any) -> str:
    if metric in {"gmv", "avg_order_value"}:
        return format_money(value)
    if metric == "repurchase_rate":
        return f"{number_or_zero(value):.2f}%"
    return format_int(value)


def number_or_zero(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
