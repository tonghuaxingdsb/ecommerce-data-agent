from __future__ import annotations

import json
from uuid import uuid4
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse

from ecommerce_agent.agent import agent_graph, stream_agent_events
from ecommerce_agent.audit import AuditLogger
from ecommerce_agent.config import get_settings
from ecommerce_agent.conversation import ConversationStore, ConversationTurn


class HealthResponse(BaseModel):
    status: str
    app_name: str
    environment: str
    llm_configured: bool


class AgentRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, examples=["近 30 天销售额多少？环比上个月呢？"])
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
    semantic_error: str
    query_spec: dict[str, Any]
    chart: dict[str, Any]
    trace: list[str]
    retrieval_sources: list[str]


def create_app() -> FastAPI:
    settings = get_settings()
    conversations = ConversationStore()
    audit_logger = AuditLogger(settings.audit_log_path)
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            app_name=settings.app_name,
            environment=settings.environment,
            llm_configured=settings.llm_provider.strip().lower() == "deepseek" and bool(settings.deepseek_api_key),
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
                ConversationTurn(
                    question=request.question,
                    answer=result["answer"],
                    intent=result["intent"],
                    query_spec=result.get("query_spec", {}),
                ),
            )
        audit_logger.log_query(
            request_id=uuid4().hex,
            conversation_id=request.conversation_id,
            question=request.question,
            intent=result["intent"],
            sql=result.get("executed_sql") or result.get("candidate_sql") or "",
            ok=not bool(result.get("sql_error")),
            row_count=result.get("row_count", 0),
            elapsed_ms=result.get("elapsed_ms", 0),
            error=result.get("sql_error", ""),
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
            semantic_error=result.get("semantic_error", ""),
            query_spec=result.get("query_spec", {}),
            chart=result.get("chart", {}),
            trace=result["trace"],
            retrieval_sources=result.get("retrieval_sources", []),
        )

    @app.post("/api/agent/query/stream")
    def stream_query_agent(request: AgentRequest) -> StreamingResponse:
        """Stream graph progress as Server-Sent Events."""
        history = conversations.get(request.conversation_id) if request.conversation_id else []

        def event_generator():
            for event in stream_agent_events(
                {
                    "question": request.question,
                    "conversation_id": request.conversation_id or "",
                    "history": history,
                    "schema_context": request.schema_context or "",
                    "metric_context": request.metric_context or "",
                    "trace": [],
                }
            ):
                if event.get("event") == "result" and request.conversation_id:
                    result = event["result"]
                    conversations.append(
                        request.conversation_id,
                        ConversationTurn(
                            question=request.question,
                            answer=result.get("answer", ""),
                            intent=result.get("intent", ""),
                            query_spec=result.get("query_spec", {}),
                        ),
                    )
                if event.get("event") == "result":
                    result = event["result"]
                    audit_logger.log_query(
                        request_id=uuid4().hex,
                        conversation_id=request.conversation_id,
                        question=request.question,
                        intent=result.get("intent", ""),
                        sql=result.get("sql", ""),
                        ok=not bool(result.get("sql_error")),
                        row_count=result.get("row_count", 0),
                        elapsed_ms=result.get("elapsed_ms", 0),
                        error=result.get("sql_error", ""),
                    )
                yield _format_sse_event(event)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _format_sse_event(event: dict[str, Any]) -> str:
    event_name = str(event.get("event") or "message")
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {payload}\n\n"


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("ecommerce_agent.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
