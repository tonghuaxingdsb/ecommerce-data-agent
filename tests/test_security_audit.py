from __future__ import annotations

import json

import pytest

from ecommerce_agent.audit import AuditLogger
from ecommerce_agent.sql_tool import SQLSafetyError, validate_readonly_sql


def test_sql_safety_rejects_foreign_schema_and_oversized_query():
    with pytest.raises(SQLSafetyError, match="schema"):
        validate_readonly_sql("SELECT * FROM pg_catalog.fact_orders")
    with pytest.raises(SQLSafetyError, match="长度"):
        validate_readonly_sql("SELECT 1 " + ("x" * 100), max_length=20)
    with pytest.raises(SQLSafetyError, match="白名单"):
        validate_readonly_sql("SELECT 1 FROM fact_orders", allowed_tables=set())


def test_audit_logger_redacts_literals_and_writes_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLogger(path).log_query(
        request_id="req-1",
        conversation_id="conv-1",
        question="token=secret ask sales",
        intent="sales_overview",
        sql="SELECT * FROM fact_orders WHERE channel = '直播' AND amount > 100",
        ok=True,
        row_count=1,
        elapsed_ms=4,
    )
    event = json.loads(path.read_text(encoding="utf-8"))
    assert event["request_id"] == "req-1"
    assert "secret" not in event["question"]
    assert "直播" not in event["sql_preview"]
    assert event["sql_sha256"]
