from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityDecision:
    task_type: str
    capability: str
    supported: bool = True
    inherit_context: bool = True
    reason: str = ""
    forecast_horizon: str | None = None


CAPABILITY_REGISTRY = {
    "semantic_sql": {
        "task_types": ("descriptive_analysis", "comparative_analysis", "diagnostic_analysis"),
        "description": "指标、维度、筛选、时间与排名的通用只读 SQL 分析",
    },
    "inventory_forecast": {
        "task_types": ("inventory_planning",),
        "description": "按商品预测未来周/月/季度/年度或指定天数的销量并结合当前库存计算建议补货量",
    },
    "unsupported_business": {
        "task_types": ("unsupported_business",),
        "description": "当前数据或工具不支持的电商分析与操作请求，明确解释缺口",
    },
    "out_of_scope": {
        "task_types": ("out_of_scope",),
        "description": "电商数据分析范围外的请求，直接说明可用能力",
    },
}


_BUSINESS_TERMS = (
    "销售", "成交", "gmv", "营收", "订单", "销量", "件数", "客单价", "复购", "商品", "货品", "sku",
    "类目", "品类", "渠道", "地区", "区域", "大区", "华东", "华南", "华北", "华中", "西南", "西北", "东北",
    "会员", "用户", "客户", "库存", "备货", "补货", "采购", "退款", "退货", "留存", "流失", "物流",
    "竞品", "投放", "广告", "利润", "毛利", "转化", "卖得", "卖了", "流水", "营业额",
)
_FOLLOW_UP_TERMS = ("环比", "同比", "贡献", "拉动", "驱动", "拆解", "再看", "继续", "刚才", "该指标")
_NON_ANALYTIC_ACTIONS = ("写一首", "写首诗", "翻译", "写代码", "写一个python", "讲笑话", "做菜", "旅游路线")
_UNSUPPORTED_DATA_TERMS = (
    "转化率", "转化", "毛利", "利润", "roi", "roas", "广告投放", "投放", "退款率", "退货率",
    "留存率", "流失", "签收", "物流", "竞品", "曝光", "点击率", "访客", "独立访客", "页面浏览量", "获客成本",
)


def match_capability(question: str) -> CapabilityDecision:
    """Map an open-ended request to a registered executable capability.

    This is deliberately a coarse task/capability match rather than a catalogue
    of user utterances.  The semantic layer still resolves metrics, dimensions,
    filters and time slots for ordinary analysis requests.
    """

    text = question.lower().replace(" ", "")
    if any(token in text for token in _NON_ANALYTIC_ACTIONS) or re.search(
        r"(?:写|生成|创作).{0,12}(?:诗|文案|故事|文章|介绍)", text
    ):
        return CapabilityDecision(
            task_type="out_of_scope", capability="out_of_scope", supported=False, inherit_context=False,
            reason="我只处理电商订单、商品、渠道、地区、会员指标分析和商品库存规划；可以改问近 30 天销售额或商品销量。",
        )
    if re.search(r"(?:导出|显示|列出|提供).*(?:手机号|手机号码|邮箱|身份证|密码)", text) or re.search(
        r"(?:删除|修改|更新|写入|清空|drop|delete|update|insert).*(?:订单|用户|商品|库存|table|from)", text
    ):
        return CapabilityDecision(
            task_type="unsupported_business", capability="unsupported_business", supported=False, inherit_context=False,
            reason="当前 agent 只做汇总分析，不提供个人联系方式或执行数据写入、删除操作。",
        )
    if any(token in text for token in _UNSUPPORTED_DATA_TERMS):
        if "转化" in text:
            reason = "当前数据只有订单，没有曝光、访问或点击数据，无法计算转化率；请补充漏斗数据或改问销售额、订单量。"
        elif "毛利" in text or "利润" in text:
            reason = "当前数据没有成本字段，无法计算利润或毛利率；请补充成本数据或改问销售额。"
        else:
            reason = "当前数据或指标层缺少该问题所需的字段和口径，无法给出可信结果；请补充相应数据与指标定义。"
        return CapabilityDecision(
            task_type="unsupported_business", capability="unsupported_business", supported=False, inherit_context=False,
            reason=reason,
        )
    if not any(token in text for token in (*_BUSINESS_TERMS, *_FOLLOW_UP_TERMS)):
        return CapabilityDecision(
            task_type="out_of_scope", capability="out_of_scope", supported=False, inherit_context=False,
            reason="我只处理电商订单、商品、渠道、地区、会员指标分析和商品库存规划；可以改问近 30 天销售额或商品销量。",
        )
    forecast_horizon = detect_forecast_horizon(question)
    inventory_action = any(token in text for token in ("备货", "补货", "采购量", "准备多少库存", "库存量"))
    future_signal = forecast_horizon is not None or any(token in text for token in ("预测", "预计", "预估", "趋势"))
    if inventory_action and future_signal:
        return CapabilityDecision(
            task_type="inventory_planning",
            capability="inventory_forecast",
            inherit_context=False,
            reason="检测到未来周期的商品备货/补货决策请求",
            forecast_horizon=forecast_horizon or "next_30_days",
        )

    forecast_signal = forecast_horizon is not None or any(token in text for token in ("预测", "预计", "预估"))
    if forecast_signal:
        return CapabilityDecision(
            task_type="forecasting",
            capability="unsupported_forecast",
            supported=False,
            inherit_context=False,
            reason="当前仅注册了按商品销量预测的库存规划能力；其他预测目标尚无可执行工具",
            forecast_horizon=forecast_horizon,
        )

    task_type = "diagnostic_analysis" if any(token in text for token in ("原因", "为什么", "贡献", "拉动", "驱动", "拆解")) else "descriptive_analysis"
    if any(token in text for token in ("同比", "环比", "对比", "相比", "变化", "增长")):
        task_type = "comparative_analysis"
    return CapabilityDecision(task_type=task_type, capability="semantic_sql")


def requires_fresh_context(question: str) -> bool:
    decision = match_capability(question)
    return not decision.inherit_context


def detect_forecast_horizon(question: str) -> str | None:
    text = question.lower().replace(" ", "")
    calendar_horizons = (
        ("next_calendar_week", ("下周", "下星期", "下个星期")),
        ("next_calendar_month", ("下个月", "下月")),
        ("next_calendar_quarter", ("下季度", "下个季度", "下一季度")),
        ("next_calendar_year", ("明年", "下一年", "下年度")),
    )
    for horizon, markers in calendar_horizons:
        if any(marker in text for marker in markers):
            return horizon

    rolling_patterns = (
        (r"(?:未来|接下来)(\d+)天", 1),
        (r"(?:未来|接下来)(\d+)周", 7),
        (r"(?:未来|接下来)(\d+)个月", 30),
    )
    for pattern, multiplier in rolling_patterns:
        matched = re.search(pattern, text)
        if matched:
            days = max(1, min(int(matched.group(1)) * multiplier, 366))
            return f"next_{days}_days"
    if "未来" in text or "接下来" in text:
        return "next_30_days"
    return None
