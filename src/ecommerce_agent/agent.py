from __future__ import annotations

from typing import Any, Callable, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from ecommerce_agent.answering import build_fallback_analysis
from ecommerce_agent.charting import build_chart_spec
from ecommerce_agent.conversation import ConversationTurn, resolve_follow_up
from ecommerce_agent.language import detect_response_language
from ecommerce_agent.llm import DeepSeekGateway, LLMResult
from ecommerce_agent.rag import retrieve_rag_context
from ecommerce_agent.schema_context import DEFAULT_SCHEMA_CONTEXT, METRIC_CONTEXT
from ecommerce_agent.sql_tool import SQLExecutionResult, execute_sql
from ecommerce_agent.text_to_sql import parse_sql_plan


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


class AgentState(TypedDict, total=False):
    question: str
    effective_question: str
    history: list[ConversationTurn | dict[str, str]]
    conversation_id: str
    schema_context: str
    metric_context: str
    intent: str
    llm_response: str
    llm_configured: bool
    plan: dict[str, Any]
    response_language: str
    candidate_sql: str
    executed_sql: str
    executable_sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    sql_error: str
    elapsed_ms: int
    retry_count: int
    max_retries: int
    chart: dict[str, Any]
    natural_language_analysis: str
    answer: str
    trace: list[str]
    retrieval_sources: list[str]


SQLRunner = Callable[[str], SQLExecutionResult]


def build_agent_graph(llm: CompletionGateway | None = None, sql_runner: SQLRunner | None = None, max_retries: int = 2):
    gateway = llm or DeepSeekGateway()
    run_sql = sql_runner or execute_sql

    def classify_intent(state: AgentState) -> AgentState:
        question = state["question"]
        effective_question = resolve_follow_up(question, state.get("history"))
        intent = infer_intent(effective_question)
        response_language = detect_response_language(question)
        return {
            **state,
            "effective_question": effective_question,
            "intent": intent,
            "response_language": response_language,
            "retry_count": state.get("retry_count", 0),
            "max_retries": state.get("max_retries", max_retries),
            "trace": [*state.get("trace", []), f"classify_intent:{intent}", f"detect_language:{response_language}"],
        }

    def prepare_context(state: AgentState) -> AgentState:
        retrieved_schema, retrieved_metrics, sources = retrieve_rag_context(state.get("effective_question", state["question"]))
        base_schema = state.get("schema_context") or DEFAULT_SCHEMA_CONTEXT
        base_metrics = state.get("metric_context") or METRIC_CONTEXT
        schema_context = f"{base_schema}\n\n相关数据字典（RAG）：\n{retrieved_schema}" if retrieved_schema else base_schema
        metric_context = f"{base_metrics}\n\n相关指标字典（RAG）：\n{retrieved_metrics}" if retrieved_metrics else base_metrics
        return {
            **state,
            "schema_context": schema_context,
            "metric_context": metric_context,
            "retrieval_sources": sources,
            "trace": [*state.get("trace", []), "prepare_context:default_schema_and_metrics"],
        }

    def generate_sql(state: AgentState) -> AgentState:
        result = gateway.complete(
            question=state.get("effective_question", state["question"]),
            schema_context=state["schema_context"],
            metric_context=state["metric_context"],
            intent=state["intent"],
            previous_sql=state.get("candidate_sql") if state.get("retry_count", 0) > 0 else None,
            sql_error=state.get("sql_error") if state.get("retry_count", 0) > 0 else None,
        )
        plan = parse_sql_plan(result.text, default_intent=state["intent"], question=state.get("effective_question", state["question"]))
        return {
            **state,
            "llm_response": result.text,
            "llm_configured": result.configured,
            "plan": plan.to_dict(),
            "candidate_sql": plan.candidate_sql,
            "trace": [*state.get("trace", []), "generate_sql:deepseek" if result.configured else "generate_sql:fallback"],
        }

    def execute_sql_node(state: AgentState) -> AgentState:
        if state.get("plan", {}).get("needs_clarification") or not state.get("candidate_sql"):
            return {
                **state,
                "trace": [*state.get("trace", []), "execute_sql:skipped"],
            }

        result = run_sql(state["candidate_sql"])
        return {
            **state,
            "executed_sql": result.sql,
            "executable_sql": result.executable_sql,
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "elapsed_ms": result.elapsed_ms,
            "sql_error": result.error or "",
            "trace": [*state.get("trace", []), "execute_sql:ok" if result.ok else "execute_sql:error"],
        }

    def prepare_retry(state: AgentState) -> AgentState:
        retry_count = state.get("retry_count", 0) + 1
        return {
            **state,
            "retry_count": retry_count,
            "trace": [*state.get("trace", []), f"react_retry:{retry_count}"],
        }

    def finalize(state: AgentState) -> AgentState:
        plan = state.get("plan", {})
        chart = build_chart_spec(plan, state.get("columns", []), state.get("rows", []))
        return {
            **state,
            "chart": chart,
            "answer": build_answer(state),
            "trace": [*state.get("trace", []), "finalize:done"],
        }

    def summarize_result(state: AgentState) -> AgentState:
        result = gateway.summarize(
            question=state.get("effective_question", state["question"]),
            intent=state["intent"],
            plan=state.get("plan", {}),
            columns=state.get("columns", []),
            rows=state.get("rows", []),
            language=state.get("response_language", "zh"),
        )
        return {
            **state,
            "natural_language_analysis": result.text,
            "llm_configured": state.get("llm_configured", False) or result.configured,
            "trace": [*state.get("trace", []), "summarize_result:deepseek" if result.configured else "summarize_result:fallback"],
        }

    def route_after_execute(state: AgentState) -> str:
        if state.get("plan", {}).get("needs_clarification") or not state.get("candidate_sql"):
            return "finalize"
        if not state.get("sql_error"):
            return "summarize_result"
        if state.get("retry_count", 0) < state.get("max_retries", max_retries):
            return "prepare_retry"
        return "finalize"

    graph = StateGraph(AgentState)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("prepare_context", prepare_context)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("execute_sql", execute_sql_node)
    graph.add_node("prepare_retry", prepare_retry)
    graph.add_node("summarize_result", summarize_result)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "classify_intent")
    graph.add_edge("classify_intent", "prepare_context")
    graph.add_edge("prepare_context", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    graph.add_conditional_edges(
        "execute_sql",
        route_after_execute,
        {
            "prepare_retry": "prepare_retry",
            "summarize_result": "summarize_result",
            "finalize": "finalize",
        },
    )
    graph.add_edge("prepare_retry", "generate_sql")
    graph.add_edge("summarize_result", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def infer_intent(question: str) -> str:
    normalized = question.lower()
    if any(keyword in normalized for keyword in ["top", "商品", "类目", "滞销", "动销", "销量"]):
        return "product_analysis"
    if any(keyword in normalized for keyword in ["用户", "新客", "老客", "复购", "会员"]):
        return "user_analysis"
    if any(keyword in normalized for keyword in ["渠道", "搜索", "推荐", "直播", "广告", "转化"]):
        return "channel_analysis"
    if any(keyword in normalized for keyword in ["同比", "环比", "趋势", "近", "上周", "本周", "本月", "上月", "上个月", "今年", "销售", "gmv"]):
        return "sales_overview"
    return "general_analysis"


def build_answer(state: AgentState) -> str:
    plan = state.get("plan", {})
    language = state.get("response_language", "zh")
    if plan.get("needs_clarification"):
        if language == "zh":
            return f"需要先澄清口径：{plan.get('clarifying_question')}"
        return f"I need to clarify the metric first: {plan.get('clarifying_question')}"

    sql_error = state.get("sql_error")
    if sql_error:
        if language == "zh":
            return f"已生成查询，但执行失败，已重试 {state.get('retry_count', 0)} 次。错误：{sql_error}"
        return f"The query failed after {state.get('retry_count', 0)} retries. Error: {sql_error}"

    analysis = state.get("natural_language_analysis")
    if analysis:
        return analysis.strip()
    return build_fallback_analysis(
        question=state["question"],
        intent=state["intent"],
        rows=state.get("rows", []),
        language=language,
    )


def format_sample_rows(rows: list[dict[str, Any]], columns: list[str], max_rows: int = 3) -> str:
    if not rows:
        return "结果为空。"
    rendered_rows = []
    for row in rows[:max_rows]:
        rendered_rows.append(", ".join(f"{column}={row.get(column)}" for column in columns[:6]))
    return "结果样例：\n" + "\n".join(rendered_rows)


agent_graph = build_agent_graph()
