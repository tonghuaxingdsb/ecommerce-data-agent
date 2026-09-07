from __future__ import annotations

import argparse
import csv
import json
import random
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from itertools import accumulate
from pathlib import Path
from typing import Any


REGIONS = [
    {"region_id": 1, "province": "上海", "city": "上海", "area": "华东", "city_tier": "一线", "weight": 8},
    {"region_id": 2, "province": "江苏", "city": "南京", "area": "华东", "city_tier": "新一线", "weight": 6},
    {"region_id": 3, "province": "浙江", "city": "杭州", "area": "华东", "city_tier": "新一线", "weight": 7},
    {"region_id": 4, "province": "广东", "city": "广州", "area": "华南", "city_tier": "一线", "weight": 7},
    {"region_id": 5, "province": "广东", "city": "深圳", "area": "华南", "city_tier": "一线", "weight": 7},
    {"region_id": 6, "province": "福建", "city": "厦门", "area": "华南", "city_tier": "二线", "weight": 4},
    {"region_id": 7, "province": "北京", "city": "北京", "area": "华北", "city_tier": "一线", "weight": 8},
    {"region_id": 8, "province": "天津", "city": "天津", "area": "华北", "city_tier": "新一线", "weight": 4},
    {"region_id": 9, "province": "山东", "city": "青岛", "area": "华北", "city_tier": "二线", "weight": 4},
    {"region_id": 10, "province": "四川", "city": "成都", "area": "西南", "city_tier": "新一线", "weight": 6},
    {"region_id": 11, "province": "重庆", "city": "重庆", "area": "西南", "city_tier": "新一线", "weight": 5},
    {"region_id": 12, "province": "湖北", "city": "武汉", "area": "华中", "city_tier": "新一线", "weight": 5},
    {"region_id": 13, "province": "湖南", "city": "长沙", "area": "华中", "city_tier": "新一线", "weight": 4},
    {"region_id": 14, "province": "陕西", "city": "西安", "area": "西北", "city_tier": "新一线", "weight": 4},
    {"region_id": 15, "province": "辽宁", "city": "沈阳", "area": "东北", "city_tier": "二线", "weight": 3},
]

CATEGORY_PRODUCTS = {
    "美妆个护": ["修护精华", "洁面乳", "防晒霜", "身体乳", "洗发水", "香氛喷雾"],
    "家居日用": ["收纳箱", "保温杯", "床品四件套", "厨房纸", "智能台灯", "空气炸锅"],
    "数码配件": ["蓝牙耳机", "手机壳", "快充套装", "机械键盘", "移动电源", "智能手环"],
    "食品饮料": ["即饮咖啡", "坚果礼盒", "低脂牛奶", "气泡水", "代餐麦片", "茶包"],
    "服饰鞋包": ["休闲卫衣", "通勤衬衫", "运动鞋", "托特包", "防晒外套", "牛仔裤"],
    "母婴玩具": ["纸尿裤", "儿童积木", "安抚玩具", "婴儿湿巾", "儿童水杯", "绘本套装"],
    "运动户外": ["瑜伽垫", "跑步腰包", "筋膜枪", "露营灯", "速干T恤", "跳绳"],
    "图书文创": ["效率手账", "国风书签", "商业传记", "签字笔套装", "拼图", "桌面摆件"],
}

CATEGORY_PRICE_RANGE = {
    "美妆个护": (39, 699),
    "家居日用": (19, 999),
    "数码配件": (29, 1299),
    "食品饮料": (9, 299),
    "服饰鞋包": (49, 899),
    "母婴玩具": (19, 599),
    "运动户外": (29, 1299),
    "图书文创": (12, 399),
}

BRANDS = ["云禾", "北桥", "鹿屿", "橙造", "拾光", "小满", "星野", "南枝", "青木", "朴作"]
CHANNELS = [("搜索", 0.28), ("推荐", 0.24), ("直播", 0.18), ("广告", 0.14), ("自然流量", 0.10), ("社群", 0.06)]
PAYMENT_METHODS = [("支付宝", 0.42), ("微信支付", 0.38), ("银行卡", 0.14), ("余额", 0.06)]
VIP_LEVELS = [("普通", 0.62), ("银卡", 0.23), ("金卡", 0.12), ("黑金", 0.03)]
ORDER_STATUS = [("paid", 0.91), ("refunded", 0.05), ("cancelled", 0.04)]
CAMPAIGN_DAYS = {(3, 8), (5, 20), (6, 18), (8, 18), (11, 11), (12, 12)}
MONEY = Decimal("0.01")


@dataclass(frozen=True)
class DatasetConfig:
    output_dir: Path = Path("data/generated")
    order_count: int = 120_000
    user_count: int = 20_000
    product_count: int = 600
    months: int = 6
    seed: int = 20260903
    end_date: date | None = None
    csv_encoding: str = "utf-8-sig"


def parse_args() -> DatasetConfig:
    parser = argparse.ArgumentParser(description="Generate realistic ecommerce seed CSV files.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--orders", type=int, default=120_000)
    parser.add_argument("--users", type=int, default=20_000)
    parser.add_argument("--products", type=int, default=600)
    parser.add_argument("--months", type=int, default=6, choices=range(3, 7))
    parser.add_argument("--seed", type=int, default=20260903)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--csv-encoding",
        default="utf-8-sig",
        choices=["utf-8-sig", "utf-8", "gb18030"],
        help="CSV output encoding. utf-8-sig is friendlier for Excel on Windows.",
    )
    args = parser.parse_args()
    return DatasetConfig(
        output_dir=args.output_dir,
        order_count=args.orders,
        user_count=args.users,
        product_count=args.products,
        months=args.months,
        seed=args.seed,
        end_date=args.end_date,
        csv_encoding=args.csv_encoding,
    )


def money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def weighted_pick(items: list[Any], weights: list[float], rng: random.Random) -> Any:
    cumulative = list(accumulate(weights))
    index = bisect_left(cumulative, rng.random() * cumulative[-1])
    return items[min(index, len(items) - 1)]


def daterange(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days + 1
    return [start_date + timedelta(days=offset) for offset in range(days)]


def day_weight(day: date) -> float:
    weight = 1.0
    if day.weekday() >= 5:
        weight *= 1.18
    if (day.month, day.day) in CAMPAIGN_DAYS:
        weight *= 3.8
    if day.day in {1, 15, 25}:
        weight *= 1.12
    return weight


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]], encoding: str) -> None:
    with path.open("w", encoding=encoding, newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate_dates(start_date: date, end_date: date) -> list[dict[str, Any]]:
    rows = []
    for day in daterange(start_date, end_date):
        iso = day.isocalendar()
        rows.append(
            {
                "date_key": day.isoformat(),
                "year": day.year,
                "quarter": (day.month - 1) // 3 + 1,
                "month": day.month,
                "week_of_year": iso.week,
                "day_of_month": day.day,
                "day_of_week": day.isoweekday(),
                "is_weekend": day.weekday() >= 5,
                "is_campaign_day": (day.month, day.day) in CAMPAIGN_DAYS,
            }
        )
    return rows


def generate_products(rng: random.Random, count: int, start_date: date) -> list[dict[str, Any]]:
    categories = list(CATEGORY_PRODUCTS)
    products = []
    for product_id in range(1, count + 1):
        category = categories[(product_id - 1) % len(categories)]
        product_type = rng.choice(CATEGORY_PRODUCTS[category])
        brand = rng.choice(BRANDS)
        min_price, max_price = CATEGORY_PRICE_RANGE[category]
        price = money(rng.triangular(min_price, max_price, min_price + (max_price - min_price) * 0.35))
        stock = rng.randint(20, 3000)
        status = weighted_pick(["active", "slow_moving", "new_arrival"], [0.78, 0.14, 0.08], rng)
        products.append(
            {
                "product_id": product_id,
                "product_name": f"{brand}{product_type}{rng.randint(1, 99):02d}",
                "category": category,
                "brand": brand,
                "price": price,
                "stock": stock,
                "shelf_date": (start_date - timedelta(days=rng.randint(5, 540))).isoformat(),
                "product_status": status,
                "_weight": (1 / (product_id**0.55)) * rng.uniform(0.75, 1.35),
            }
        )
    rng.shuffle(products)
    return products


def generate_users(rng: random.Random, count: int, start_date: date, end_date: date) -> list[dict[str, Any]]:
    region_weights = [region["weight"] for region in REGIONS]
    users = []
    for user_id in range(1, count + 1):
        region = weighted_pick(REGIONS, region_weights, rng)
        if rng.random() < 0.82:
            register_date = start_date - timedelta(days=rng.randint(1, 540))
        else:
            register_date = start_date + timedelta(days=rng.randint(0, (end_date - start_date).days))
        vip_level = weighted_pick([level for level, _ in VIP_LEVELS], [weight for _, weight in VIP_LEVELS], rng)
        users.append(
            {
                "user_id": user_id,
                "register_date": register_date.isoformat(),
                "province": region["province"],
                "city": region["city"],
                "region_id": region["region_id"],
                "vip_level": vip_level,
            }
        )
    users.sort(key=lambda row: row["register_date"])
    return users


def generate_orders(
    rng: random.Random,
    count: int,
    dates: list[date],
    users: list[dict[str, Any]],
    products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    date_weights = [day_weight(day) for day in dates]
    order_dates = [weighted_pick(dates, date_weights, rng) for _ in range(count)]
    order_dates.sort()

    product_weights = [product["_weight"] for product in products]
    region_lookup = {region["region_id"]: region for region in REGIONS}
    register_dates = [date.fromisoformat(user["register_date"]) for user in users]
    seen_users: set[int] = set()
    paid_gmv = Decimal("0")
    paid_orders = 0
    rows = []

    for order_id, order_date in enumerate(order_dates, start=1):
        eligible_count = max(1, bisect_right(register_dates, order_date))
        user = users[rng.randrange(eligible_count)]
        product = weighted_pick(products, product_weights, rng)
        quantity = weighted_pick([1, 2, 3, 4, 5], [0.62, 0.22, 0.10, 0.04, 0.02], rng)
        gross_amount = money(Decimal(product["price"]) * Decimal(quantity))
        discount_rate = weighted_pick([0, 0.05, 0.10, 0.15, 0.20], [0.48, 0.22, 0.17, 0.09, 0.04], rng)
        if (order_date.month, order_date.day) in CAMPAIGN_DAYS:
            discount_rate = max(discount_rate, 0.10)
        discount_amount = money(gross_amount * Decimal(str(discount_rate)))
        status = weighted_pick([name for name, _ in ORDER_STATUS], [weight for _, weight in ORDER_STATUS], rng)
        amount = Decimal("0.00") if status == "cancelled" else money(gross_amount - discount_amount)
        is_new_user = user["user_id"] not in seen_users
        seen_users.add(user["user_id"])
        channel = weighted_pick([name for name, _ in CHANNELS], [weight for _, weight in CHANNELS], rng)
        payment_method = weighted_pick([name for name, _ in PAYMENT_METHODS], [weight for _, weight in PAYMENT_METHODS], rng)
        region = region_lookup[int(user["region_id"])]

        if status == "paid":
            paid_orders += 1
            paid_gmv += amount

        rows.append(
            {
                "order_id": order_id,
                "user_id": user["user_id"],
                "product_id": product["product_id"],
                "region_id": region["region_id"],
                "order_date": order_date.isoformat(),
                "quantity": quantity,
                "amount": amount,
                "discount_amount": discount_amount,
                "channel": channel,
                "payment_method": payment_method,
                "is_new_user": is_new_user,
                "status": status,
            }
        )

    summary = {
        "orders": count,
        "paid_orders": paid_orders,
        "paid_gmv": str(paid_gmv.quantize(MONEY)),
        "unique_buyers": len(seen_users),
    }
    return rows, summary


def generate_dataset(config: DatasetConfig) -> dict[str, Any]:
    if config.order_count < 1 or config.user_count < 1 or config.product_count < 1:
        raise ValueError("orders, users and products must all be positive.")

    rng = random.Random(config.seed)
    end_date = config.end_date or date.today()
    start_date = end_date - timedelta(days=config.months * 30 - 1)
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    date_days = daterange(start_date, end_date)
    date_rows = generate_dates(start_date, end_date)
    region_rows = [{key: value for key, value in region.items() if key != "weight"} for region in REGIONS]
    products = generate_products(rng, config.product_count, start_date)
    users = generate_users(rng, config.user_count, start_date, end_date)
    orders, order_summary = generate_orders(rng, config.order_count, date_days, users, products)

    product_rows = [{key: value for key, value in product.items() if not key.startswith("_")} for product in products]
    write_csv(output_dir / "dim_date.csv", list(date_rows[0].keys()), date_rows, config.csv_encoding)
    write_csv(output_dir / "dim_region.csv", list(region_rows[0].keys()), region_rows, config.csv_encoding)
    write_csv(output_dir / "dim_product.csv", list(product_rows[0].keys()), product_rows, config.csv_encoding)
    write_csv(output_dir / "dim_user.csv", list(users[0].keys()), users, config.csv_encoding)
    write_csv(output_dir / "fact_orders.csv", list(orders[0].keys()), orders, config.csv_encoding)

    summary = {
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        "months": config.months,
        "users": config.user_count,
        "products": config.product_count,
        "regions": len(REGIONS),
        "seed": config.seed,
        "csv_encoding": config.csv_encoding,
        **order_summary,
    }
    with (output_dir / "seed_summary.json").open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def main() -> None:
    summary = generate_dataset(parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
