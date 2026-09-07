from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


TABLES = [
    ("dim_date", "date_key,year,quarter,month,week_of_year,day_of_month,day_of_week,is_weekend,is_campaign_day"),
    ("dim_region", "region_id,province,city,area,city_tier"),
    ("dim_product", "product_id,product_name,category,brand,price,stock,shelf_date,product_status"),
    ("dim_user", "user_id,register_date,province,city,region_id,vip_level"),
    (
        "fact_orders",
        "order_id,user_id,product_id,region_id,order_date,quantity,amount,discount_amount,channel,payment_method,is_new_user,status",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load generated ecommerce CSV data into PostgreSQL.")
    parser.add_argument("--data-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--schema", type=Path, default=Path("sql/schema.sql"))
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--csv-encoding", default="utf-8-sig")
    parser.add_argument("--skip-schema", action="store_true")
    return parser.parse_args()


def copy_csv(cursor: psycopg.Cursor, table_name: str, columns: str, path: Path, encoding: str) -> None:
    with cursor.copy(f"COPY {table_name} ({columns}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE)") as copy:
        with path.open("r", encoding=encoding, newline="") as file:
            for line in file:
                copy.write(line)


def main() -> None:
    load_dotenv()
    args = parse_args()
    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required. Copy .env.example to .env or pass --database-url.")

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            if not args.skip_schema:
                cursor.execute(args.schema.read_text(encoding="utf-8"))
            cursor.execute(
                "TRUNCATE fact_orders, dim_user, dim_product, dim_region, dim_date RESTART IDENTITY CASCADE"
            )
            for table_name, columns in TABLES:
                csv_path = args.data_dir / f"{table_name}.csv"
                if not csv_path.exists():
                    raise FileNotFoundError(f"Missing generated file: {csv_path}")
                copy_csv(cursor, table_name, columns, csv_path, args.csv_encoding)
                cursor.execute(f"SELECT count(*) FROM {table_name}")
                row_count = cursor.fetchone()[0]
                print(f"{table_name}: {row_count} rows")


if __name__ == "__main__":
    main()
