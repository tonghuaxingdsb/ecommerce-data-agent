from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ecommerce_agent.agent import agent_graph
from ecommerce_agent.config import get_settings
from ecommerce_agent.conversation import ConversationStore, ConversationTurn


class HealthResponse(BaseModel):
    status: str
    app_name: str
    environment: str
    llm_configured: bool


class AgentRequest(BaseModel):
    question: str = Field(..., min_length=1, examples=["近 30 天销售额多少？环比上个月呢？"])
    conversation_id: str | None = Field(default=None, description="用于多轮追问的会话 ID")
    schema_context: str | None = None
    metric_context: str | None = None


class AgentResponse(BaseModel):
    question: str
    conversation_id: str | None
    intent: str
    answer: str
    llm_configured: bool
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    elapsed_ms: int
    sql_error: str
    chart: dict[str, Any]
    trace: list[str]
    retrieval_sources: list[str]


def create_app() -> FastAPI:
    settings = get_settings()
    conversations = ConversationStore()
    app = FastAPI(title=settings.app_name, version="0.1.0")

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            app_name=settings.app_name,
            environment=settings.environment,
            llm_configured=bool(settings.deepseek_api_key),
        )

    @app.post("/api/agent/query", response_model=AgentResponse)
    def query_agent(request: AgentRequest) -> AgentResponse:
        history = conversations.get(request.conversation_id) if request.conversation_id else []
        result = agent_graph.invoke(
            {
                "question": request.question,
                "conversation_id": request.conversation_id or "",
                "history": history,
                "schema_context": request.schema_context or "",
                "metric_context": request.metric_context or "",
                "trace": [],
            }
        )
        if request.conversation_id:
            conversations.append(
                request.conversation_id,
                ConversationTurn(question=request.question, answer=result["answer"], intent=result["intent"]),
            )
        return AgentResponse(
            question=result["question"],
            conversation_id=request.conversation_id,
            intent=result["intent"],
            answer=result["answer"],
            llm_configured=result["llm_configured"],
            sql=result.get("executed_sql") or result.get("candidate_sql") or "",
            columns=result.get("columns", []),
            rows=result.get("rows", []),
            row_count=result.get("row_count", 0),
            elapsed_ms=result.get("elapsed_ms", 0),
            sql_error=result.get("sql_error", ""),
            chart=result.get("chart", {}),
            trace=result["trace"],
            retrieval_sources=result.get("retrieval_sources", []),
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("ecommerce_agent.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
