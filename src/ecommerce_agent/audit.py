from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any


class AuditLogger:
    """Append-only JSONL audit logger with lightweight secret redaction."""

    def __init__(self, path: str | Path = "logs/audit.jsonl") -> None:
        self.path = Path(path)
        self._lock = Lock()

    def log_query(
        self,
        *,
        request_id: str,
        question: str,
        intent: str,
        sql: str,
        ok: bool,
        row_count: int = 0,
        elapsed_ms: int = 0,
        error: str = "",
        conversation_id: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "agent_query",
            "request_id": request_id,
            "conversation_id": conversation_id,
            "question": _redact_text(question, 500),
            "intent": intent,
            "sql_sha256": sha256(sql.encode("utf-8")).hexdigest()[:16] if sql else "",
            "sql_preview": _redact_sql(sql),
            "ok": ok,
            "row_count": row_count,
            "elapsed_ms": elapsed_ms,
            "error": _redact_text(error, 500),
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)


def _redact_text(value: str, limit: int) -> str:
    redacted = re.sub(r"(?i)(api[_-]?key|token|password)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", value or "")
    return redacted[:limit]


def _redact_sql(sql: str) -> str:
    # Keep the shape useful for debugging while avoiding literal user values.
    preview = re.sub(r"'(?:''|[^'])*'", "'[REDACTED]'", sql or "")
    preview = re.sub(r"\b\d+(?:\.\d+)?\b", "?", preview)
    return preview[:1000]

