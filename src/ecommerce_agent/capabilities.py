from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityDecision:
    task_type: str
    capability: str
    supported: bool = True
    inherit_context: bool = True
    reason: str = ""


CAPABILITY_REGISTRY = {
    "semantic_sql": {
        "task_types": ("descriptive_analysis", "comparative_analysis", "diagnostic_analysis"),
        "description": "指标、维度、筛选、时间与排名的通用只读 SQL 分析",
    },
    "inventory_forecast": {
        "task_types": ("inventory_planning",),
        "description": "按商品预测下个自然月销量并结合当前库存计算建议补货量",
    },
}


def match_capability(question: str) -> CapabilityDecision:
    """Map an open-ended request to a registered executable capability.

    This is deliberately a coarse task/capability match rather than a catalogue
    of user utterances.  The semantic layer still resolves metrics, dimensions,
    filters and time slots for ordinary analysis requests.
    """

    text = question.lower().replace(" ", "")
    inventory_action = any(token in text for token in ("备货", "补货", "采购量", "准备多少库存", "库存量"))
    future_signal = any(token in text for token in ("预测", "预计", "预估", "未来", "下个月", "下月", "趋势"))
    if inventory_action and future_signal:
        return CapabilityDecision(
            task_type="inventory_planning",
            capability="inventory_forecast",
            inherit_context=False,
            reason="检测到未来周期的商品备货/补货决策请求",
        )

    forecast_signal = any(token in text for token in ("预测", "预计", "预估", "未来", "下个月", "下月"))
    if forecast_signal:
        return CapabilityDecision(
            task_type="forecasting",
            capability="unsupported_forecast",
            supported=False,
            inherit_context=False,
            reason="当前仅注册了按商品销量预测的库存规划能力；其他预测目标尚无可执行工具",
        )

    task_type = "diagnostic_analysis" if any(token in text for token in ("原因", "为什么", "贡献", "拉动", "驱动", "拆解")) else "descriptive_analysis"
    if any(token in text for token in ("同比", "环比", "对比", "相比", "变化", "增长")):
        task_type = "comparative_analysis"
    return CapabilityDecision(task_type=task_type, capability="semantic_sql")


def requires_fresh_context(question: str) -> bool:
    decision = match_capability(question)
    return not decision.inherit_context
