from __future__ import annotations

from typing import Any


def build_chart_spec(plan: dict[str, Any], columns: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    requested_type = str(plan.get("chart_type") or "table")
    x_column = plan.get("chart_x")
    y_column = plan.get("chart_y")
    numeric = [column for column in columns if any(isinstance(row.get(column), (int, float)) for row in rows)]
    categorical = [column for column in columns if column not in numeric]
    if requested_type in {"bar", "line", "pie"} and x_column in columns and y_column in columns:
        chart_type = requested_type
    elif "date" in " ".join(columns).lower() and numeric:
        chart_type, x_column, y_column = "line", next((c for c in columns if "date" in c.lower()), columns[0]), numeric[0]
    elif categorical and numeric and len(rows) <= 30:
        chart_type, x_column, y_column = "bar", categorical[0], numeric[0]
    else:
        chart_type, x_column, y_column = "table", None, None
    return {
        "type": chart_type,
        "x": x_column,
        "y": y_column,
        "title": plan.get("summary") or "查询结果",
        "data_points": len(rows),
    }

