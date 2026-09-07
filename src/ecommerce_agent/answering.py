from __future__ import annotations

from typing import Any

from ecommerce_agent.time_intelligence import parse_time_intent


def build_fallback_analysis(question: str, intent: str, rows: list[dict[str, Any]], language: str) -> str:
    if language == "zh":
        return build_chinese_analysis(question, intent, rows)
    return build_english_analysis(question, intent, rows)


def build_chinese_analysis(question: str, intent: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "这次查询没有返回数据。建议先放宽时间范围或确认筛选条件是否过窄。"

    first = rows[0]
    if intent == "sales_overview":
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


def number_or_zero(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
