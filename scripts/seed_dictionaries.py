from __future__ import annotations

import argparse

import psycopg

from ecommerce_agent.config import Settings
from ecommerce_agent.rag import DATA_DICTIONARY, METRIC_DICTIONARY


def seed_dictionaries(database_url: str) -> tuple[int, int]:
    data_count = 0
    metric_count = 0
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for item in DATA_DICTIONARY:
                cursor.execute(
                    """
                    INSERT INTO data_dictionary (table_name, column_name, description, synonyms)
                    SELECT %s, %s, %s, %s
                    WHERE NOT EXISTS (
                      SELECT 1 FROM data_dictionary
                      WHERE table_name = %s AND column_name = %s
                    )
                    """,
                    (item.table_name, item.column_name, item.description, list(item.synonyms), item.table_name, item.column_name),
                )
                data_count += cursor.rowcount
            for item in METRIC_DICTIONARY:
                cursor.execute(
                    """
                    INSERT INTO metric_dictionary
                      (metric_name, display_name, description, expression_sql, filter_sql, synonyms)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (metric_name) DO UPDATE SET
                      display_name = EXCLUDED.display_name,
                      description = EXCLUDED.description,
                      expression_sql = EXCLUDED.expression_sql,
                      filter_sql = EXCLUDED.filter_sql,
                      synonyms = EXCLUDED.synonyms
                    """,
                    (item.name, item.name, item.description, item.expression_sql, item.filter_sql, list(item.synonyms)),
                )
                metric_count += cursor.rowcount
        connection.commit()
    return data_count, metric_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed ecommerce data and metric dictionaries")
    parser.add_argument("--database-url", default=Settings().database_url)
    args = parser.parse_args()
    data_count, metric_count = seed_dictionaries(args.database_url)
    print(f"dictionary seed complete: data={data_count}, metrics={metric_count}")


if __name__ == "__main__":
    main()

