from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any

import psycopg
from psycopg.rows import dict_row
from sqlglot import exp, parse

from ecommerce_agent.config import Settings, get_settings


ALLOWED_TABLES = {
    "dim_date",
    "dim_region",
    "dim_product",
    "dim_user",
    "fact_orders",
    "data_dictionary",
    "metric_dictionary",
}

FORBIDDEN_EXPRESSIONS = (
    exp.Alter,
    exp.Command,
    exp.Create,
    exp.Delete,
    exp.Drop,
    exp.Insert,
    exp.Merge,
    exp.Update,
)

DANGEROUS_FUNCTIONS = {
    "pg_read_file",
    "pg_write_file",
    "pg_sleep",
    "lo_import",
    "lo_export",
    "dblink",
    "current_setting",
    "set_config",
}


@dataclass(frozen=True)
class SafeSQL:
    original_sql: str
    executable_sql: str
    tables: tuple[str, ...]


@dataclass(frozen=True)
class SQLExecutionResult:
    ok: bool
    sql: str
    executable_sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    elapsed_ms: int
    error: str | None = None


class SQLSafetyError(ValueError):
    pass


def validate_readonly_sql(sql: str, max_rows: int = 200, allowed_tables: set[str] | None = None, max_length: int = 20000) -> SafeSQL:
    normalized_sql = strip_trailing_semicolon(sql)
    if not normalized_sql:
        raise SQLSafetyError("SQL 为空。")
    if max_rows < 1:
        raise SQLSafetyError("结果行数上限必须大于 0。")
    if "\x00" in normalized_sql:
        raise SQLSafetyError("SQL 包含非法控制字符。")
    if len(normalized_sql) > max_length:
        raise SQLSafetyError(f"SQL 超过长度限制（最多 {max_length} 个字符）。")

    expressions = [expression for expression in parse(normalized_sql, read="postgres") if expression is not None]
    if len(expressions) != 1:
        raise SQLSafetyError("只允许执行单条 SQL。")

    expression = expressions[0]
    if not is_read_query(expression):
        raise SQLSafetyError("只允许执行 SELECT/WITH 只读查询。")
    if any(expression.find_all(*FORBIDDEN_EXPRESSIONS)):
        raise SQLSafetyError("SQL 中包含写入或结构变更语句。")

    function_names = {
        str(function.name).lower()
        for function in expression.find_all(exp.Func)
        if getattr(function, "name", None)
    }
    blocked_functions = sorted(function_names.intersection(DANGEROUS_FUNCTIONS))
    if blocked_functions:
        raise SQLSafetyError(f"SQL 中包含禁止函数：{', '.join(blocked_functions)}。")

    whitelist = ALLOWED_TABLES if allowed_tables is None else allowed_tables
    cte_names = {cte.alias_or_name for cte in expression.find_all(exp.CTE) if cte.alias_or_name}
    table_expressions = [table for table in expression.find_all(exp.Table) if table.name not in cte_names]
    foreign_schemas = sorted({str(table.db) for table in table_expressions if table.db and str(table.db).lower() != "public"})
    if foreign_schemas:
        raise SQLSafetyError(f"SQL 引用了非白名单 schema：{', '.join(foreign_schemas)}。")
    referenced_tables = tuple(sorted({table.name for table in table_expressions}))
    unknown_tables = [table for table in referenced_tables if table not in whitelist]
    if unknown_tables:
        raise SQLSafetyError(f"SQL 引用了非白名单表：{', '.join(unknown_tables)}。")

    executable_sql = f"SELECT * FROM ({normalized_sql}) AS agent_limited_result LIMIT {max_rows}"
    return SafeSQL(original_sql=normalized_sql, executable_sql=executable_sql, tables=referenced_tables)


def execute_sql(sql: str, settings: Settings | None = None) -> SQLExecutionResult:
    runtime_settings = settings or get_settings()
    started_at = perf_counter()
    try:
        safe_sql = validate_readonly_sql(sql, max_rows=runtime_settings.sql_max_rows, max_length=runtime_settings.sql_max_length)
        with psycopg.connect(runtime_settings.database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cursor:
                cursor.execute("BEGIN READ ONLY")
                cursor.execute(f"SET LOCAL statement_timeout = {int(runtime_settings.sql_timeout_ms)}")
                cursor.execute(safe_sql.executable_sql)
                rows = [serialize_row(row) for row in cursor.fetchall()]
                columns = [column.name for column in cursor.description or []]
                cursor.execute("COMMIT")
        return SQLExecutionResult(
            ok=True,
            sql=safe_sql.original_sql,
            executable_sql=safe_sql.executable_sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            elapsed_ms=elapsed_ms(started_at),
        )
    except Exception as exc:
        return SQLExecutionResult(
            ok=False,
            sql=strip_trailing_semicolon(sql),
            executable_sql="",
            columns=[],
            rows=[],
            row_count=0,
            elapsed_ms=elapsed_ms(started_at),
            error=str(exc),
        )


def is_read_query(expression: exp.Expression) -> bool:
    return isinstance(expression, (exp.Select, exp.Union, exp.Intersect, exp.Except))


def strip_trailing_semicolon(sql: str) -> str:
    stripped = sql.strip()
    while stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    return stripped


def elapsed_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: serialize_value(value) for key, value in row.items()}


def serialize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
