from __future__ import annotations

import ecommerce_agent.main as main_module
from fastapi.testclient import TestClient


def test_sse_endpoint_streams_trace_and_result(monkeypatch):
    def fake_stream(input_state):
        assert input_state["question"] == "近 30 天销售额"
        yield {"event": "trace", "node": "classify_intent", "trace": "classify_intent:sales_overview"}
        yield {
            "event": "result",
            "node": "finalize",
            "result": {"question": input_state["question"], "intent": "sales_overview", "answer": "ok", "rows": [], "columns": [], "trace": []},
        }

    monkeypatch.setattr(main_module, "stream_agent_events", fake_stream)
    client = TestClient(main_module.create_app())
    response = client.post("/api/agent/query/stream", json={"question": "近 30 天销售额", "conversation_id": "sse-test"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: trace" in response.text
    assert "event: result" in response.text


def test_api_rejects_unbounded_question():
    client = TestClient(main_module.create_app())
    response = client.post("/api/agent/query/stream", json={"question": "x" * 2001})
    assert response.status_code == 422


def test_api_allows_nextjs_development_origin():
    client = TestClient(main_module.create_app())
    response = client.options(
        "/api/agent/query/stream",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
