from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from openai import OpenAI

from ecommerce_agent.answering import build_fallback_analysis
from ecommerce_agent.config import Settings, get_settings
from ecommerce_agent.text_to_sql import FEW_SHOTS, fallback_sql_plan


SYSTEM_PROMPT = """
你是一个面向电商运营的数据分析 Agent。
你的任务是把用户的自然语言问题转化为可执行分析计划，必要时给出候选 SQL。
请遵守这些规则：
1. 只基于给定 schema 和指标口径回答，不编造表或字段。
2. 如果问题中的口径不清晰，优先提出一个澄清问题。
3. SQL 只能使用 SELECT，不生成 DELETE、DROP、UPDATE、INSERT、ALTER。
4. 默认统计销售额/GMV 时只看 status = 'paid'。
5. 先把问题拆成 query_spec，再生成 SQL。query_spec 必须包含 task_type、capability、metrics、dimensions、filters、time_range、sort_by、sort_direction、limit、minimum_sample_size、analysis_type、comparison。
6. 指标和维度必须分别建模。例如“什么商品复购率高”是 metric=repurchase_rate、dimension=product，不能退化成 GMV 商品排名。
7. 输出简洁 json，字段包含 intent、needs_clarification、clarifying_question、analysis_plan、query_spec、candidate_sql、summary、chart_type、chart_x、chart_y。
8. candidate_sql 必须是 PostgreSQL 方言；除非用户要求明细，否则优先聚合，避免返回超大结果。
9. 复购率必须先在目标维度内按 user_id 统计去重 paid 订单数，再用 paid_orders >= 2 的用户数除以购买用户数。
10. 多轮追问中，当前问题没有重新指定的指标、时间和筛选条件必须沿用上一轮；“贡献来源、拉动、驱动”应按当前周期与比较周期的指标增量拆解，而不是只按当前值排行。
11. 不要把预测、优化或决策问题退化成描述性 GMV 查询；没有注册能力时应明确说明缺少的工具或数据。
12. “各月、每月、按月、月度”使用 order_month 维度，按订单月份聚合。月份是分组粒度，time_range 则应来自用户指定或上一轮的时间范围。
""".strip()


@dataclass(frozen=True)
class LLMResult:
    text: str
    configured: bool


class CompletionGateway(Protocol):
    @property
    def is_configured(self) -> bool: ...

    def complete(
        self,
        question: str,
        schema_context: str,
        metric_context: str,
        intent: str,
        previous_sql: str | None = None,
        sql_error: str | None = None,
    ) -> LLMResult: ...

    def summarize(
        self,
        question: str,
        intent: str,
        plan: dict[str, Any],
        columns: list[str],
        rows: list[dict[str, Any]],
        language: str,
    ) -> LLMResult: ...


class DeepSeekGateway:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = (
            OpenAI(
                api_key=self.settings.deepseek_api_key,
                base_url=self.settings.deepseek_base_url,
                timeout=self.settings.llm_request_timeout_ms / 1000,
            )
            if self.settings.deepseek_api_key
            else None
        )

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    def complete(
        self,
        question: str,
        schema_context: str,
        metric_context: str,
        intent: str,
        previous_sql: str | None = None,
        sql_error: str | None = None,
    ) -> LLMResult:
        if not self._client:
            return LLMResult(
                text=self._mock_response(question=question, intent=intent),
                configured=False,
            )

        response = self._client.chat.completions.create(
            model=self.settings.deepseek_model,
            max_tokens=self.settings.deepseek_max_tokens,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": self._build_user_prompt(
                        question=question,
                        schema_context=schema_context,
                        metric_context=metric_context,
                        intent=intent,
                        previous_sql=previous_sql,
                        sql_error=sql_error,
                    ),
                }
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        return LLMResult(text=content.strip(), configured=True)

    def summarize(
        self,
        question: str,
        intent: str,
        plan: dict[str, Any],
        columns: list[str],
        rows: list[dict[str, Any]],
        language: str,
    ) -> LLMResult:
        if not self._client:
            return LLMResult(
                text=build_fallback_analysis(
                    question=question,
                    intent=intent,
                    rows=rows,
                    language=language,
                    query_spec=plan.get("query_spec"),
                ),
                configured=False,
            )

        response = self._client.chat.completions.create(
            model=self.settings.deepseek_model,
            max_tokens=self.settings.deepseek_max_tokens,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个电商运营数据分析师。基于查询结果给业务结论，不能编造结果中没有的数据，"
                        "不要输出 SQL。用户用中文提问就用中文回答，用户用英文提问就用英文回答。"
                    ),
                },
                {
                    "role": "user",
                    "content": self._build_summary_prompt(
                        question=question,
                        intent=intent,
                        plan=plan,
                        columns=columns,
                        rows=rows,
                        language=language,
                    ),
                },
            ],
        )
        content = response.choices[0].message.content or ""
        return LLMResult(text=content.strip(), configured=True)

    def _build_user_prompt(
        self,
        question: str,
        schema_context: str,
        metric_context: str,
        intent: str,
        previous_sql: str | None,
        sql_error: str | None,
    ) -> str:
        repair_context = ""
        if previous_sql and sql_error:
            repair_context = f"""
上一次 SQL 执行失败，请修复后重新输出完整 json。
失败 SQL：
{previous_sql}

错误信息：
{sql_error}
""".strip()

        return f"""
用户问题：
{question}

初步意图：
{intent}

Schema：
{schema_context}

指标口径：
{metric_context}

few-shot 示例：
{FEW_SHOTS}

{repair_context}
""".strip()

    def _mock_response(self, question: str, intent: str) -> str:
        plan = fallback_sql_plan(question, intent)
        return json.dumps(plan.to_dict(), ensure_ascii=False, indent=2)

    def _build_summary_prompt(
        self,
        question: str,
        intent: str,
        plan: dict[str, Any],
        columns: list[str],
        rows: list[dict[str, Any]],
        language: str,
    ) -> str:
        compact_rows = rows[:20]
        response_language = "中文" if language == "zh" else "English"
        return f"""
用户问题：
{question}

回答语言：
{response_language}

业务意图：
{intent}

分析计划摘要：
{json.dumps(plan, ensure_ascii=False)}

查询列：
{json.dumps(columns, ensure_ascii=False)}

查询结果样例：
{json.dumps(compact_rows, ensure_ascii=False)}

请输出面向运营同学的自然语言分析，要求：
1. 先直接回答问题。
2. 引用关键数值和排序/对比结果。
3. 说明一个可能的业务判断或下一步拆解方向。
4. 不要输出 SQL，不要提“数据库返回了几行”作为主要结论。
5. 如果计划包含 assumptions，必须明确说明这些是假设；库存建议不能被描述为确定的采购指令。
        """.strip()


class RuleBasedGateway(DeepSeekGateway):
    """Explicit offline provider used for demos, tests and disconnected hosts."""

    def __init__(self, settings: Settings | None = None) -> None:
        runtime = settings or get_settings()
        # Avoid creating an OpenAI client even if a key happens to be present.
        self.settings = runtime
        self._client = None


def build_gateway(settings: Settings | None = None) -> CompletionGateway:
    runtime = settings or get_settings()
    provider = runtime.llm_provider.lower().strip()
    if provider in {"mock", "rule_based", "offline"}:
        return RuleBasedGateway(runtime)
    if provider == "deepseek":
        return DeepSeekGateway(runtime)
    raise ValueError(f"不支持的 LLM_PROVIDER：{runtime.llm_provider}")
