from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import date
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate_seed_data.py"
spec = importlib.util.spec_from_file_location("generate_seed_data", SCRIPT_PATH)
generate_seed_data = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = generate_seed_data
spec.loader.exec_module(generate_seed_data)
DatasetConfig = generate_seed_data.DatasetConfig
generate_dataset = generate_seed_data.generate_dataset


def row_count(path):
    with path.open("r", encoding="utf-8", newline="") as file:
        return sum(1 for _ in csv.DictReader(file))


def test_generate_dataset_writes_expected_files(tmp_path):
    summary = generate_dataset(
        DatasetConfig(
            output_dir=tmp_path,
            order_count=80,
            user_count=30,
            product_count=16,
            months=3,
            seed=7,
            end_date=date(2026, 8, 31),
            csv_encoding="utf-8-sig",
        )
    )

    assert summary["orders"] == 80
    assert summary["months"] == 3
    assert row_count(tmp_path / "fact_orders.csv") == 80
    assert row_count(tmp_path / "dim_user.csv") == 30
    assert row_count(tmp_path / "dim_product.csv") == 16
    assert (tmp_path / "seed_summary.json").exists()
