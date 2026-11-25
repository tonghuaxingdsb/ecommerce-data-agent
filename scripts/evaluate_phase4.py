"""Run 100 business questions plus 10 out-of-scope cases against the agent.

Usage: uv run python -m scripts.evaluate_phase4 --provider mock --output reports/phase4-mock.json
       uv run python -m scripts.evaluate_phase4 --provider deepseek --output reports/phase4-deepseek.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from threading import local
from typing import Any

import psycopg

from ecommerce_agent.agent import build_agent_graph
from ecommerce_agent.config import get_settings
from ecommerce_agent.eval_cases import CASES
from ecommerce_agent.llm import SYSTEM_PROMPT, DeepSeekGateway, RuleBasedGateway
from ecommerce_agent.sql_tool import execute_sql
from ecommerce_agent.text_to_sql import FEW_SHOTS


ORACLE_CATEGORIES = {"scalar_time", "breakdown_rank", "daily_trend", "area_filter", "business_edge"}
DIMENSIONS = {
    "product": ("p.product_name", "dim_product p ON p.product_id = o.product_id", "product_name"),
    "category": ("p.category", "dim_product p ON p.product_id = o.product_id", "category"),
    "channel": ("o.channel", "", "channel"),
    "area": ("r.area", "dim_region r ON r.region_id = o.region_id", "area"),
    "vip_level": ("u.vip_level", "dim_user u ON u.user_id = o.user_id", "vip_level"),
    "order_date": ("o.order_date", "", "order_date"),
}
METRICS = {
    "gmv": "SUM(o.amount) AS gmv",
    "orders": "COUNT(DISTINCT o.order_id) AS orders",
    "units": "SUM(o.quantity) AS units",
    "avg_order_value": "ROUND(SUM(o.amount) / NULLIF(COUNT(DISTINCT o.order_id), 0), 2) AS avg_order_value",
}


def oracle_sql(case: dict[str, Any]) -> str | None:
    """Independent SQL oracle for simple aggregates, including paid-user repeats."""
    if case["category"] in {"comparison", "contribution"} or (case["category"] == "follow_up" and case["expected"].get("analysis_type") == "contribution"):
        return period_oracle_sql(case)
    if case["category"] == "business_edge" and "滞销" in case["question"]:
        return (
            "SELECT p.product_name, p.category, p.stock, COALESCE(SUM(o.quantity), 0) AS units, "
            "COALESCE(SUM(o.amount), 0) AS gmv FROM dim_product p "
            "LEFT JOIN fact_orders o ON o.product_id = p.product_id AND o.status = 'paid' "
            "AND o.order_date >= (SELECT max(order_date) - INTERVAL '29 days' FROM fact_orders) "
            "GROUP BY p.product_name, p.category, p.stock "
            "HAVING COALESCE(SUM(o.quantity), 0) < 20 AND p.stock > 500 "
            "ORDER BY units ASC, p.stock DESC LIMIT 10"
        )
    if case["category"] not in ORACLE_CATEGORIES and not (case["category"] == "follow_up" and "analysis_type" not in case["expected"]):
        return None
    expected = case["expected"]
    metric = expected["metrics"][0]
    dimensions = expected.get("dimensions", [])
    field, dimension_join, alias = DIMENSIONS[dimensions[0]] if dimensions else ("", "", "")
    joins = [f"JOIN {dimension_join}"] if dimension_join else []
    if expected.get("area") and not any("dim_region" in join for join in joins):
        joins.append("JOIN dim_region r ON r.region_id = o.region_id")
    where = ["o.status = 'paid'"]
    time_range = expected.get("time_range")
    if time_range:
        if time_range.startswith("近 "):
            days = int(time_range.split()[1])
            where.append(f"o.order_date BETWEEN anchor.last_date - INTERVAL '{days - 1} days' AND anchor.last_date")
        elif time_range == "上周":
            where.append("o.order_date >= date_trunc('week', anchor.last_date - INTERVAL '7 days')")
            where.append("o.order_date < date_trunc('week', anchor.last_date)")
        elif time_range == "本月":
            where.append("o.order_date >= date_trunc('month', anchor.last_date)")
            where.append("o.order_date < date_trunc('month', anchor.last_date) + INTERVAL '1 month'")
        elif time_range == "上个月":
            where.append("o.order_date >= date_trunc('month', anchor.last_date - INTERVAL '1 month')")
            where.append("o.order_date < date_trunc('month', anchor.last_date)")
        elif time_range == "今年":
            where.append("o.order_date >= date_trunc('year', anchor.last_date)")
            where.append("o.order_date < date_trunc('year', anchor.last_date) + INTERVAL '1 year'")
        elif time_range == "本周":
            where.append("o.order_date >= date_trunc('week', anchor.last_date)")
            where.append("o.order_date < date_trunc('week', anchor.last_date) + INTERVAL '1 week'")
        else:
            return None
    if expected.get("area"):
        where.append(f"r.area = '{expected['area']}'")
    from_sql = "FROM fact_orders o CROSS JOIN (SELECT max(order_date) AS last_date FROM fact_orders) anchor"
    if joins:
        from_sql += " " + " ".join(joins)
    where_sql = " AND ".join(where)
    group_sql = f" GROUP BY {field}" if field else ""
    if metric == "repurchase_rate":
        inner_fields = f"{field} AS {alias}, " if field else ""
        inner_group = f"{field}, " if field else ""
        outer_fields = f"{alias}, " if field else ""
        outer_group = f" GROUP BY {alias} HAVING COUNT(*) >= 20" if field else ""
        sql = (f"WITH user_counts AS (SELECT {inner_fields}o.user_id, COUNT(DISTINCT o.order_id) AS n "
               f"{from_sql} WHERE {where_sql} GROUP BY {inner_group}o.user_id) "
               f"SELECT {outer_fields}COUNT(*) AS paid_users, COUNT(*) FILTER (WHERE n >= 2) AS repeat_users, "
               f"ROUND(100.0 * COUNT(*) FILTER (WHERE n >= 2) / NULLIF(COUNT(*), 0), 2) AS repurchase_rate_pct "
               f"FROM user_counts{outer_group}")
        metric_column = "repurchase_rate_pct"
    else:
        select_dimension = f"{field} AS {alias}, " if field else ""
        metric_selects = ", ".join(METRICS[item] for item in expected["metrics"])
        sql = f"SELECT {select_dimension}{metric_selects} {from_sql} WHERE {where_sql}{group_sql}"
        metric_column = metric
    if field and (expected.get("sort_direction") or expected.get("limit")):
        sql += f" ORDER BY {metric_column} {str(expected.get('sort_direction') or 'desc').upper()}"
        if metric == "repurchase_rate":
            sql += ", paid_users DESC"
    if expected.get("limit") and field:
        sql += f" LIMIT {expected['limit']}"
    return sql


def period_oracle_sql(case: dict[str, Any]) -> str:
    expected = case["expected"]
    period = expected["time_range"]
    comparison = expected["comparison"]
    if period.startswith("近 "):
        days = int(period.split()[1])
        current = f"o.order_date BETWEEN a.last_date - INTERVAL '{days - 1} days' AND a.last_date"
        if comparison == "yoy":
            previous = f"o.order_date BETWEEN a.last_date - INTERVAL '{days + 364} days' AND a.last_date - INTERVAL '365 days'"
        else:
            previous = f"o.order_date BETWEEN a.last_date - INTERVAL '{2 * days - 1} days' AND a.last_date - INTERVAL '{days} days'"
    elif period == "上个月":
        current = "o.order_date >= date_trunc('month', a.last_date - INTERVAL '1 month') AND o.order_date < date_trunc('month', a.last_date)"
        previous = "o.order_date >= date_trunc('month', a.last_date - INTERVAL '2 months') AND o.order_date < date_trunc('month', a.last_date - INTERVAL '1 month')"
    else:
        current = "o.order_date >= date_trunc('month', a.last_date) AND o.order_date < date_trunc('month', a.last_date) + INTERVAL '1 month'"
        previous = "o.order_date >= date_trunc('month', a.last_date - INTERVAL '1 year') AND o.order_date < date_trunc('month', a.last_date - INTERVAL '1 year') + INTERVAL '1 month'"
    anchor = "CROSS JOIN (SELECT max(order_date) AS last_date FROM fact_orders) a"
    if case["category"] == "comparison":
        return (
            "WITH periods AS (SELECT "
            f"SUM(o.amount) FILTER (WHERE {current}) AS current_gmv, "
            f"COUNT(DISTINCT o.order_id) FILTER (WHERE {current}) AS current_orders, "
            f"SUM(o.quantity) FILTER (WHERE {current}) AS current_units, "
            f"SUM(o.amount) FILTER (WHERE {previous}) AS previous_gmv, "
            f"COUNT(DISTINCT o.order_id) FILTER (WHERE {previous}) AS previous_orders, "
            f"SUM(o.quantity) FILTER (WHERE {previous}) AS previous_units "
            f"FROM fact_orders o {anchor} WHERE o.status = 'paid') "
            "SELECT current_gmv, current_orders, current_units, previous_gmv, previous_orders, previous_units, "
            "ROUND((current_gmv - previous_gmv) / NULLIF(previous_gmv, 0) * 100, 2) AS change_pct, "
            "ROUND((current_gmv - previous_gmv) / NULLIF(previous_gmv, 0) * 100, 2) AS gmv_mom_pct, "
            "ROUND(current_gmv / NULLIF(current_orders, 0), 2) AS current_avg_order_value FROM periods"
        )
    dimension = expected["dimensions"][0]
    field, join, alias = DIMENSIONS[dimension]
    join_sql = f"JOIN {join}" if join else ""
    limit = expected.get("limit") or 10
    return (
        "WITH amounts AS (SELECT "
        f"{field} AS {alias}, "
        f"COALESCE(SUM(CASE WHEN {current} THEN o.amount ELSE 0 END), 0) AS current_gmv, "
        f"COALESCE(SUM(CASE WHEN {previous} THEN o.amount ELSE 0 END), 0) AS previous_gmv "
        f"FROM fact_orders o {anchor} {join_sql} WHERE o.status = 'paid' GROUP BY {field}), "
        "changes AS (SELECT *, ROUND(current_gmv - previous_gmv, 2) AS gmv_change, "
        "ROUND((current_gmv - previous_gmv) / NULLIF(previous_gmv, 0) * 100, 2) AS change_pct FROM amounts) "
        "SELECT *, ROUND(gmv_change / NULLIF(SUM(gmv_change) OVER (), 0) * 100, 2) AS contribution_pct "
        f"FROM changes ORDER BY gmv_change DESC LIMIT {limit}"
    )


@lru_cache(maxsize=1)
def inventory_daily_data(database_url: str) -> tuple[date, dict[int, tuple[str, int, tuple[int, ...]]]]:
    """Fetch only source observations; the forecast arithmetic runs in Python."""
    query = """
SELECT p.product_id, p.product_name, p.stock, d.day::date,
       COALESCE(SUM(o.quantity), 0)::int AS daily_units
FROM dim_product p
CROSS JOIN (SELECT max(order_date)::date AS last_date FROM fact_orders) anchor
CROSS JOIN LATERAL generate_series(anchor.last_date - INTERVAL '89 days', anchor.last_date, INTERVAL '1 day') d(day)
LEFT JOIN fact_orders o ON o.product_id = p.product_id AND o.order_date = d.day::date AND o.status = 'paid'
WHERE p.product_status = 'active'
GROUP BY p.product_id, p.product_name, p.stock, d.day
ORDER BY p.product_id, d.day
""".strip()
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT max(order_date)::date FROM fact_orders")
            last_date = cursor.fetchone()[0]
            cursor.execute(query)
            source = cursor.fetchall()
    grouped: dict[int, tuple[str, int, list[int]]] = {}
    for product_id, product_name, stock, _, daily_units in source:
        if product_id not in grouped:
            grouped[product_id] = (product_name, stock, [])
        grouped[product_id][2].append(daily_units)
    return last_date, {key: (name, stock, tuple(units)) for key, (name, stock, units) in grouped.items()}


def inventory_reference_rows(case: dict[str, Any], database_url: str, max_rows: int) -> list[dict[str, Any]]:
    last_date, products = inventory_daily_data(database_url)
    horizon = case["expected"]["forecast_horizon"]

    def month_start(offset: int) -> date:
        month_index = last_date.year * 12 + last_date.month - 1 + offset
        return date(month_index // 12, month_index % 12 + 1, 1)

    if horizon == "next_calendar_week":
        start = last_date + timedelta(days=7 - last_date.weekday())
        end = start + timedelta(days=6)
    elif horizon == "next_calendar_month":
        start, end = month_start(1), month_start(2) - timedelta(days=1)
    elif horizon == "next_calendar_quarter":
        quarter_offset = 3 - ((last_date.month - 1) % 3)
        start, end = month_start(quarter_offset), month_start(quarter_offset + 3) - timedelta(days=1)
    elif horizon == "next_calendar_year":
        start, end = date(last_date.year + 1, 1, 1), date(last_date.year + 1, 12, 31)
    else:
        days = int(horizon.removeprefix("next_").removesuffix("_days"))
        start, end = last_date + timedelta(days=1), last_date + timedelta(days=days)
    horizon_days = (end - start).days + 1
    first_offset = (start - last_date).days
    rows = []
    for product_name, stock, daily in products.values():
        n = len(daily)
        if n != 90:
            raise ValueError(f"Expected 90 daily observations for {product_name}, got {n}")
        total = sum(daily)
        sum_x = n * (n - 1) // 2
        sum_x2 = (n - 1) * n * (2 * n - 1) // 6
        sum_xy = sum(index * units for index, units in enumerate(daily))
        avg = (Decimal(total) / Decimal(n)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        slope = (Decimal(n * sum_xy - sum_x * total) / Decimal(n * sum_x2 - sum_x * sum_x)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        forecast_total = sum(max(Decimal(0), avg + slope * Decimal(first_offset + index)) for index in range(horizon_days))
        forecast_units = int(forecast_total.to_integral_value(rounding=ROUND_CEILING))
        safety = int((Decimal(forecast_units) / Decimal(horizon_days) * Decimal(7)).to_integral_value(rounding=ROUND_CEILING))
        target = forecast_units + safety
        restock = max(target - stock, 0)
        if restock:
            rows.append({
                "product_name": product_name, "forecast_start": start.isoformat(), "forecast_end": end.isoformat(),
                "current_stock": stock, "avg_daily_units": float(avg), "trend_units_per_day": float(slope),
                "forecast_units": forecast_units, "safety_stock_units": safety, "target_stock": target,
                "recommended_restock": restock,
            })
    rows.sort(key=lambda item: (-item["recommended_restock"], -item["forecast_units"], item["product_name"]))
    return rows[:max_rows]


def same_rows(actual: list[dict[str, Any]], reference: list[dict[str, Any]], expected: dict[str, Any]) -> bool:
    dimensions = expected.get("dimensions", [])
    metric = expected["metrics"][0]
    fields = [DIMENSIONS[item][2] for item in dimensions]
    if expected.get("capability") == "inventory_forecast":
        fields = ["product_name", "forecast_start", "forecast_end", "current_stock", "avg_daily_units", "trend_units_per_day", "forecast_units", "safety_stock_units", "target_stock", "recommended_restock"]
    elif expected.get("comparison") and not dimensions:
        fields = ["current_gmv", "current_orders", "current_units", "previous_gmv", "previous_orders", "previous_units", "change_pct", "gmv_mom_pct", "current_avg_order_value"]
    elif expected.get("analysis_type") == "contribution":
        fields.extend(("current_gmv", "previous_gmv", "gmv_change", "change_pct", "contribution_pct"))
    elif expected.get("oracle_kind") == "slow_moving":
        fields.extend(("category", "stock", "units", "gmv"))
    else:
        fields.extend("repurchase_rate_pct" if item == "repurchase_rate" else item for item in expected["metrics"])
        if metric == "repurchase_rate":
            fields.extend(("paid_users", "repeat_users"))
    if len(actual) != len(reference):
        return False

    def normalized(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        items = []
        for row in rows:
            values = []
            for field in fields:
                value = row.get(field)
                if isinstance(value, (int, float, Decimal)):
                    digits = 6 if field == "trend_units_per_day" else 4 if field == "avg_daily_units" else 2
                    value = round(float(value), digits)
                if isinstance(value, (date, datetime)):
                    value = value.isoformat()
                values.append(value)
            items.append(tuple(values))
        return sorted(items, key=str)

    return normalized(actual) == normalized(reference)


def score_case(case: dict[str, Any], result: dict[str, Any], reference_rows: list[dict[str, Any]] | None, reference_error: str | None) -> dict[str, Any]:
    expected = case["expected"]
    spec = result.get("query_spec") or {}
    outcome = case["outcome"]
    plan_checks: dict[str, bool] = {}
    for key in ("capability", "metrics", "dimensions", "time_range", "comparison", "analysis_type", "limit", "sort_direction", "forecast_horizon"):
        if key in expected:
            actual = spec.get(key)
            plan_checks[key] = list(actual or []) == expected[key] if key in ("metrics", "dimensions") else actual == expected[key]
    if "area" in expected:
        plan_checks["area"] = any(item.get("field") == "area" and item.get("value") == expected["area"] for item in spec.get("filters", []))
    plan_ok = all(plan_checks.values())
    no_sql = not bool(result.get("candidate_sql") or result.get("executed_sql"))
    if outcome == "refuse":
        execution_ok = no_sql and result.get("row_count", 0) == 0 and bool(result.get("answer"))
        result_ok = None
    else:
        execution_ok = bool(result.get("candidate_sql")) and "execute_sql:ok" in result.get("trace", []) and not result.get("sql_error") and not result.get("semantic_error")
        result_ok = same_rows(result.get("rows", []), reference_rows, expected) if reference_rows is not None else None
    correct = plan_ok and execution_ok and (result_ok is not False) and reference_error is None
    record = {
        "id": case["id"], "category": case["category"], "question": case["question"],
        "expected_outcome": outcome, "actual_capability": spec.get("capability"),
        "plan_ok": plan_ok, "failed_slots": [key for key, ok in plan_checks.items() if not ok],
        "execution_ok": execution_ok, "result_ok": result_ok, "reference_error": reference_error,
        "correct": correct, "sql_error": result.get("sql_error", ""), "semantic_error": result.get("semantic_error", ""),
    }
    if not correct:
        record["actual_query_spec"] = spec
        record["candidate_sql"] = result.get("candidate_sql", "")
        record["answer"] = result.get("answer", "")
        record["actual_rows_sample"] = result.get("rows", [])[:3]
        record["reference_rows_sample"] = (reference_rows or [])[:3]
    return record


def evaluate_case(case: dict[str, Any], graph: Any, database_url: str, max_rows: int) -> dict[str, Any]:
    expected = case["expected"]
    history = []
    if case["category"] == "follow_up":
        previous_spec = {"metrics": expected["metrics"], "time_range": expected["time_range"], "comparison": expected.get("comparison")}
        history = [{"question": expected["previous_question"], "query_spec": previous_spec}]
    try:
        result = graph.invoke({"question": case["question"], "history": history, "trace": []})
    except Exception as exc:
        return {"id": case["id"], "category": case["category"], "question": case["question"], "correct": False, "exception": str(exc)}
    reference_rows = None
    reference_error = None
    if result.get("candidate_sql"):
        if case["category"] == "inventory":
            try:
                reference_rows = inventory_reference_rows(case, database_url, max_rows)
            except Exception as exc:
                reference_error = str(exc)
        else:
            reference = oracle_sql(case)
            if reference:
                oracle_result = execute_sql(reference)
                if oracle_result.ok:
                    reference_rows = oracle_result.rows
                else:
                    reference_error = oracle_result.error
    return score_case(case, result, reference_rows, reference_error)


def run(provider: str, limit: int | None = None, ids: set[str] | None = None, workers: int = 1) -> dict[str, Any]:
    settings = get_settings()
    if provider == "deepseek" and not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required for --provider deepseek")
    snapshot = execute_sql("SELECT COUNT(*) AS order_count, MAX(order_date) AS max_order_date FROM fact_orders")
    if not snapshot.ok or not snapshot.rows or not snapshot.rows[0].get("order_count"):
        raise RuntimeError(f"A seeded PostgreSQL database is required: {snapshot.error or 'fact_orders is empty'}")
    cases = [case for case in CASES if case["id"] in ids] if ids else CASES[:limit] if limit else CASES
    if any(case["category"] == "inventory" for case in cases):
        inventory_daily_data(settings.database_url)
    thread_state = local()

    def work(case: dict[str, Any]) -> dict[str, Any]:
        if not hasattr(thread_state, "graph"):
            gateway = RuleBasedGateway(settings) if provider == "mock" else DeepSeekGateway(settings)
            thread_state.graph = build_agent_graph(llm=gateway)
        return evaluate_case(case, thread_state.graph, settings.database_url, settings.sql_max_rows)

    records = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        for index, record in enumerate(executor.map(work, cases), 1):
            records.append(record)
            if index == 1 or index % 10 == 0 or index == len(cases):
                print(f"Evaluated {index}/{len(cases)}: {record['id']}", file=sys.stderr, flush=True)
    return summarize_records(provider, settings.deepseek_model if provider == "deepseek" else "rule_based", snapshot.rows[0], records)


def summarize_records(provider: str, model: str, data_snapshot: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["category"]].append(record)
    total = len(records)
    business_records = [item for item in records if item["category"] != "out_of_scope"]
    out_of_scope_records = [item for item in records if item["category"] == "out_of_scope"]
    report = {
        "benchmark": "phase4-100-business-10-ood", "provider": provider, "model": model,
        "dataset_sha256": sha256(json.dumps(CASES, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "prompt_sha256": sha256((SYSTEM_PROMPT + "\n" + FEW_SHOTS).encode("utf-8")).hexdigest(),
        "data_snapshot": data_snapshot,
        "evaluated_at": datetime.now(timezone.utc).isoformat(), "cases": total,
        "accuracy": round(sum(bool(item["correct"]) for item in records) / total, 4) if total else 0,
        "business_cases": len(business_records),
        "business_accuracy": round(sum(bool(item["correct"]) for item in business_records) / len(business_records), 4) if business_records else None,
        "out_of_scope_cases": len(out_of_scope_records),
        "out_of_scope_accuracy": round(sum(bool(item["correct"]) for item in out_of_scope_records) / len(out_of_scope_records), 4) if out_of_scope_records else None,
        "plan_accuracy": round(sum(bool(item.get("plan_ok")) for item in records) / total, 4) if total else 0,
        "execution_or_refusal_rate": round(sum(bool(item.get("execution_ok")) for item in records) / total, 4) if total else 0,
        "result_equivalence": {
            "passed": sum(item.get("result_ok") is True for item in records),
            "eligible": sum(item.get("result_ok") is not None for item in records),
        },
        "by_category": {name: {"correct": sum(bool(item["correct"]) for item in group), "total": len(group)} for name, group in groups.items()},
        "records": records,
    }
    return report


def merge_reports(paths: list[Path]) -> dict[str, Any]:
    """Combine prior full and targeted runs, verifying every fixed case label."""
    source_reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    provider = source_reports[0]["provider"]
    model = source_reports[0]["model"]
    expected_cases = {case["id"]: case for case in CASES}
    selected: dict[str, dict[str, Any]] = {}
    for source in source_reports:
        if source["provider"] != provider or source["model"] != model:
            raise ValueError("Cannot merge different providers or models")
        for record in source["records"]:
            case = expected_cases.get(record["id"])
            if case is None or record["question"] != case["question"] or record["category"] != case["category"]:
                raise ValueError(f"Case label changed: {record['id']}")
            selected[record["id"]] = record
    missing = [case["id"] for case in CASES if case["id"] not in selected]
    if missing:
        raise ValueError(f"Missing cases: {', '.join(missing)}")
    snapshots = [source.get("data_snapshot") for source in source_reports if source.get("data_snapshot")]
    if snapshots and any(item != snapshots[0] for item in snapshots):
        raise ValueError("Cannot merge reports from different data snapshots")
    report = summarize_records(provider, model, snapshots[0] if snapshots else {}, [selected[case["id"]] for case in CASES])
    report["source_runs"] = [{"path": str(path), "evaluated_at": source.get("evaluated_at")} for path, source in zip(paths, source_reports)]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("mock", "deepseek"), default="mock")
    parser.add_argument("--limit", type=int, default=None, help="Smoke-test the first N cases")
    parser.add_argument("--ids", default=None, help="Comma-separated case IDs for targeted reruns")
    parser.add_argument("--workers", type=int, default=1, help="Parallel independent agent sessions (DeepSeek: 3-4 recommended)")
    parser.add_argument("--merge", nargs="+", type=Path, help="Merge full and targeted reports; later files override earlier case IDs")
    parser.add_argument("--export-cases", type=Path, help="Write the fixed labelled benchmark as JSONL and exit")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.export_cases:
        args.export_cases.parent.mkdir(parents=True, exist_ok=True)
        args.export_cases.write_text(
            "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in CASES), encoding="utf-8"
        )
        print(f"Exported {len(CASES)} cases to {args.export_cases}")
        return
    report = merge_reports(args.merge) if args.merge else run(args.provider, args.limit, set(args.ids.split(",")) if args.ids else None, args.workers)
    summary = {key: value for key, value in report.items() if key != "records"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
