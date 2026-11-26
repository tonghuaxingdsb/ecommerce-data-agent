from __future__ import annotations

import pytest
import psycopg

from ecommerce_agent.config import Settings
from ecommerce_agent.sql_tool import SQLSafetyError, execute_sql, validate_readonly_sql


DATABASE_URL = "postgresql://agent:agent@localhost:5432/ecommerce_agent"


def test_validate_readonly_sql_wraps_query_with_limit():
    safe_sql = validate_readonly_sql("SELECT order_date FROM fact_orders;", max_rows=5)

    assert safe_sql.tables == ("fact_orders",)
    assert safe_sql.executable_sql.endswith("LIMIT 5")


def test_validate_readonly_sql_allows_cte_aliases():
    safe_sql = validate_readonly_sql(
        "WITH recent_orders AS (SELECT * FROM fact_orders) SELECT count(*) FROM recent_orders",
        max_rows=5,
    )

    assert safe_sql.tables == ("fact_orders",)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM fact_orders",
        "UPDATE fact_orders SET amount = 0",
        "SELECT * FROM fact_orders; SELECT * FROM dim_user",
        "SELECT * FROM pg_user",
    ],
)
def test_validate_readonly_sql_rejects_unsafe_queries(sql):
    with pytest.raises(SQLSafetyError):
        validate_readonly_sql(sql)


def test_execute_sql_reads_postgres_when_available():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available.")

    result = execute_sql(
        "SELECT product_name, category FROM dim_product ORDER BY product_id",
        settings=Settings(database_url=DATABASE_URL, sql_max_rows=2, sql_timeout_ms=3000),
    )

    assert result.ok is True
    assert result.row_count == 2
    assert result.columns == ["product_name", "category"]
    assert result.rows[0]["product_name"]


@pytest.mark.parametrize("sql", ["SELECT (1", "SELECT 'unterminated"])
def test_parse_failures_are_safety_errors_without_escape_sequences(sql):
    with pytest.raises(SQLSafetyError, match="SQL 无法解析") as exc_info:
        validate_readonly_sql(sql)
    assert "\x1b" not in str(exc_info.value)

    result = execute_sql(sql)
    assert result.error == str(exc_info.value)


@pytest.mark.parametrize("suffix", [" -- count orders", "; -- count orders", "; /* count orders */"])
def test_execute_sql_preserves_query_with_trailing_comment(suffix):
    sql = "SELECT '--;' AS marker" + suffix
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1):
            pass
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available.")

    result = execute_sql(sql, settings=Settings(database_url=DATABASE_URL))
    assert result.ok, result.error
    assert result.rows == [{"marker": "--;"}]
