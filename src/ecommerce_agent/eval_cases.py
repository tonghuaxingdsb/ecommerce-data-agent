"""Hand-authored Phase 4 benchmark: 100 business + 10 out-of-scope cases.

The matrices vary metric, dimension, time and area independently.  Every row
contains an explicit expected route and semantic slots; this module is the
source of truth for both offline and model-backed evaluations.
"""

from __future__ import annotations

from typing import Any


METRICS = (
    ("销售额", "gmv"),
    ("订单量", "orders"),
    ("销量", "units"),
    ("客单价", "avg_order_value"),
    ("复购率", "repurchase_rate"),
)


def build_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def add(category: str, question: str, outcome: str = "execute", **expected: Any) -> None:
        cases.append({
            "id": f"P4-{len(cases) + 1:03d}",
            "category": category,
            "question": question,
            "outcome": outcome,
            "expected": expected,
        })

    # 20 scalar questions: each metric across rolling and calendar windows.
    for label, metric in METRICS:
        for time_text, time_label in (("近7天", "近 7 天"), ("近30天", "近 30 天"), ("上周", "上周"), ("本月", "本月")):
            add("scalar_time", f"{time_text}{label}是多少？", metrics=[metric], dimensions=[], time_range=time_label)

    # 25 ranked breakdowns, including the more costly user-level repurchase metric.
    breakdowns = (
        ("商品", "product"), ("类目", "category"), ("渠道", "channel"),
        ("地区", "area"), ("会员等级", "vip_level"),
    )
    for label, metric in METRICS:
        for phrase, dimension in breakdowns:
            wording = f"近30天各{phrase}{label}排名前10" if phrase == "地区" else f"近30天按{phrase}统计{label}，排名前10"
            add("breakdown_rank", wording, metrics=[metric], dimensions=[dimension], time_range="近 30 天", limit=10, sort_direction="desc")

    # 5 daily-series queries; these require a date dimension, not a scalar sum.
    for label, metric in METRICS:
        add("daily_trend", f"近30天每日{label}趋势", metrics=[metric], dimensions=["order_date"], time_range="近 30 天")

    # 15 regional filters with no region breakdown.
    for label, metric in METRICS:
        for area in ("华东", "华南", "华北"):
            add("area_filter", f"近30天{area}{label}是多少？", metrics=[metric], dimensions=[], time_range="近 30 天", area=area)

    # 8 comparative/diagnostic questions.  Their plan and execution are scored;
    # result-equivalence uses the independent oracle only for simple aggregates.
    for question, comparison, time_range in (
        ("近30天销售额环比上期变化多少？", "mom", "近 30 天"),
        ("近7天销售额环比上期变化多少？", "mom", "近 7 天"),
        ("上个月销售额环比上期变化多少？", "mom", "上个月"),
        ("本月销售额同比去年同期变化多少？", "yoy", "本月"),
    ):
        add("comparison", question, metrics=["gmv"], dimensions=[], comparison=comparison, time_range=time_range)
    for phrase, dimension in breakdowns[:4]:
        wording = f"近30天{phrase}销售额增长贡献排名前10" if phrase != "地区" else "近30天各地区销售额增长贡献排名前10"
        add("contribution", wording, metrics=["gmv"], dimensions=[dimension], time_range="近 30 天", analysis_type="contribution", comparison="mom", limit=10)

    # 5 registered inventory planning horizons.
    for question, horizon in (
        ("下周各商品应该补货多少？", "next_calendar_week"),
        ("下月各商品应该准备多少库存量？", "next_calendar_month"),
        ("下季度各商品应该准备多少库存？", "next_calendar_quarter"),
        ("明年各商品需要备货多少？", "next_calendar_year"),
        ("未来45天各商品预计补货多少？", "next_45_days"),
    ):
        add("inventory", question, capability="inventory_forecast", metrics=["units"], dimensions=["product"], forecast_horizon=horizon)

    # 2 follow-ups with independently labelled inherited slots.
    add("follow_up", "主要贡献来源是什么商品？", previous_question="近30天销售额环比上期变化多少？",
        metrics=["gmv"], dimensions=["product"], time_range="近 30 天", comparison="mom", analysis_type="contribution")
    add("follow_up", "那按渠道分别看呢？", previous_question="近30天订单量是多少？",
        metrics=["orders"], dimensions=["channel"], time_range="近 30 天")

    # 10 business requests whose metric, data or executable capability is absent.
    for question in (
        "哪个渠道转化率最高？", "近30天毛利率是多少？", "预测下个月销售额是多少？",
        "各渠道广告投放 ROI 是多少？", "上月退款率是多少？", "用户30日留存率是多少？",
        "物流平均签收时长是多少？", "明天哪些用户会流失？", "分析竞品价格变化",
        "请导出所有用户的手机号和邮箱",
    ):
        capability = "unsupported_forecast" if question.startswith("预测") else "unsupported_business"
        add("unsupported_business", question, "refuse", capability=capability)

    # 10 requests unrelated to ecommerce analytics.  They must never reach SQL.
    for question in (
        "今天天气怎么样？", "给我写一首诗", "把这句话翻译成英文", "世界杯决赛谁赢了？",
        "帮我写一个 Python 爬虫", "总统是谁？", "我发烧了该吃什么药？",
        "计算 123 乘以 456", "红烧肉怎么做？", "推荐一条周末旅游路线",
    ):
        add("out_of_scope", question, "refuse", capability="out_of_scope")

    # 10 additional business edge cases: synonyms, ascending ranks, multiple
    # metrics, short trend windows, mixed region filters and legacy slow movers.
    for question, expected in (
        ("近14天GMV是多少？", {"metrics": ["gmv"], "dimensions": [], "time_range": "近 14 天"}),
        ("上个月成交额是多少？", {"metrics": ["gmv"], "dimensions": [], "time_range": "上个月"}),
        ("今年订单数是多少？", {"metrics": ["orders"], "dimensions": [], "time_range": "今年"}),
        ("近30天销量最低的商品前5", {"metrics": ["units"], "dimensions": ["product"], "time_range": "近 30 天", "sort_direction": "asc", "limit": 5}),
        ("近30天各类目客单价排名前3", {"metrics": ["avg_order_value"], "dimensions": ["category"], "time_range": "近 30 天", "limit": 3}),
        ("近7天每日订单量趋势", {"metrics": ["orders"], "dimensions": ["order_date"], "time_range": "近 7 天", "sort_direction": "asc"}),
        ("近30天华中商品销量前5", {"metrics": ["units"], "dimensions": ["product"], "time_range": "近 30 天", "area": "华中", "limit": 5}),
        ("近30天GMV和订单量分别是多少？", {"metrics": ["orders", "gmv"], "dimensions": [], "time_range": "近 30 天"}),
        ("本周各渠道复购率排名前5", {"metrics": ["repurchase_rate"], "dimensions": ["channel"], "time_range": "本周", "limit": 5}),
        ("近30天哪些商品滞销", {"metrics": ["units"], "dimensions": ["product"], "time_range": "近 30 天", "sort_direction": "asc", "limit": 10, "oracle_kind": "slow_moving"}),
    ):
        add("business_edge", question, **expected)

    assert len(cases) == 110
    assert len({case["question"] for case in cases}) == 110
    return cases


CASES = build_cases()
