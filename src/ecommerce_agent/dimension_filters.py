from __future__ import annotations

import re

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError


DIMENSION_FILTER_LABELS = {
    "area": "地区",
    "channel": "渠道",
    "category": "类目",
    "vip_level": "会员等级",
    "brand": "品牌",
}

DIMENSION_FILTER_COLUMNS = {
    "area": "r.area",
    "channel": "o.channel",
    "category": "p.category",
    "vip_level": "u.vip_level",
    "brand": "p.brand",
}

DIMENSION_FILTER_JOINS = {
    "area": "JOIN dim_region r ON r.region_id = o.region_id",
    "category": "JOIN dim_product p ON p.product_id = o.product_id",
    "vip_level": "JOIN dim_user u ON u.user_id = o.user_id",
    "brand": "JOIN dim_product p ON p.product_id = o.product_id",
}

_LABELS = {
    "area": r"地区|区域|大区",
    "channel": r"(?:流量)?渠道",
    "category": r"商品类目|类目|品类",
    "vip_level": r"会员等级|VIP\s*等级|VIP\s*会员|会员|用户",
    "brand": r"商品品牌|品牌",
}
_VALUES = {
    "area": dict.fromkeys(("华东", "华南", "华北", "西南", "华中", "西北", "东北")),
    "channel": dict.fromkeys(("搜索", "推荐", "直播", "广告", "自然流量", "社群")),
    "category": {
        **dict.fromkeys(("美妆个护", "家居日用", "数码配件", "食品饮料", "服饰鞋包", "母婴玩具", "运动户外", "图书文创")),
        "美妆": "美妆个护",
        "个护": "美妆个护",
        "家居": "家居日用",
        "数码": "数码配件",
        "食品": "食品饮料",
        "服饰": "服饰鞋包",
        "母婴": "母婴玩具",
        "图书": "图书文创",
    },
    "vip_level": dict.fromkeys(("普通", "银卡", "金卡", "黑金")),
    "brand": dict.fromkeys(("云禾", "北桥", "鹿屿", "橙造", "拾光", "小满", "星野", "南枝", "青木", "朴作")),
}
_LABEL_REQUIRED = {"channel": {"搜索", "推荐", "广告"}, "vip_level": {"普通"}}
_QUOTED_VALUE = r'''[“「\x22](?P<value>[^”」\x22\r\n]+)[”」\x22]'''


def dimension_filter_value(field: str, value: str) -> str:
    return _VALUES[field].get(value) or value


def extract_dimension_filters(question: str) -> tuple[dict[str, str], str, tuple[str, ...]]:
    """Return equality scopes, question with value phrases masked, and ambiguous fields."""
    filters: dict[str, str] = {}
    ambiguous: list[str] = []
    spans: list[tuple[int, int]] = []
    for field, candidates in _VALUES.items():
        labels = _LABELS[field]
        values = "|".join(map(re.escape, sorted(candidates, key=len, reverse=True)))
        value = rf"(?P<value>{values})"
        patterns = (
            rf"(?:{labels})\s*(?:为|是|等于|[:：=])?\s*{_QUOTED_VALUE}",
            rf"{_QUOTED_VALUE}\s*(?:的\s*)?(?:{labels})",
            rf"(?:{labels})\s*(?:为|是|等于|[:：=])?\s*{value}",
            rf"{value}\s*(?:的\s*)?(?:{labels})",
        )
        standalone = [
            name for name in candidates
            if name not in _LABEL_REQUIRED.get(field, ()) and candidates[name] is None
        ]
        if standalone:
            bare = "|".join(map(re.escape, sorted(standalone, key=len, reverse=True)))
            patterns += (rf"(?P<value>{bare})",)

        matches: list[re.Match[str]] = []
        for pattern in patterns:
            for match in re.finditer(pattern, question, re.IGNORECASE):
                if any(match.start() < other.end() and match.end() > other.start() for other in matches):
                    continue
                matches.append(match)
        resolved = dict.fromkeys(dimension_filter_value(field, match["value"].strip()) for match in matches)
        if len(resolved) > 1:
            ambiguous.append(field)
        elif resolved and (value := next(iter(resolved))):
            filters[field] = value
        spans.extend(match.span() for match in matches)

    masked_question = list(question)
    for start, end in spans:
        masked_question[start:end] = " " * (end - start)
    return filters, "".join(masked_question), tuple(ambiguous)


def dimension_filter_sql(field: str, value: str) -> str:
    literal = exp.Literal.string(value).sql(dialect="postgres")
    return f"{DIMENSION_FILTER_COLUMNS[field]} = {literal}"


def dimension_filter_equalities(sql: str) -> set[tuple[str, str]]:
    """Read required column equalities from WHERE predicates, including CTEs."""
    try:
        expression = parse_one(sql, read="postgres")
    except SqlglotError:
        return set()
    equalities: set[tuple[str, str]] = set()
    pending = [where.this for where in expression.find_all(exp.Where)]
    while pending:
        condition = pending.pop().unnest()
        if isinstance(condition, exp.And):
            pending.extend((condition.this, condition.expression))
        elif isinstance(condition, exp.EQ):
            left, right = condition.this, condition.expression
            if isinstance(right, exp.Column):
                left, right = right, left
            if isinstance(left, exp.Column) and isinstance(right, exp.Literal) and right.is_string:
                equalities.add((left.name.lower(), right.this))
    return equalities
