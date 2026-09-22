from __future__ import annotations

"""Small, dependency-light retrieval layer for the data and metric dictionaries.

The production database has pgvector columns, but local development and tests do
not need an embedding service.  We therefore keep a deterministic lexical
retriever as the fallback and expose a pgvector search helper for callers that
already have an embedding from their model provider.
"""

import re
from dataclasses import dataclass
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from ecommerce_agent.config import Settings, get_settings


@dataclass(frozen=True)
class DictionaryItem:
    kind: str
    name: str
    description: str
    table_name: str | None = None
    column_name: str | None = None
    expression_sql: str | None = None
    filter_sql: str | None = None
    synonyms: tuple[str, ...] = ()

    def render(self) -> str:
        if self.kind == "metric":
            filter_text = f"，过滤 {self.filter_sql}" if self.filter_sql else ""
            return f"- {self.name}：{self.description}；SQL 表达式：{self.expression_sql}{filter_text}"
        location = f"{self.table_name}.{self.column_name}" if self.column_name else str(self.table_name)
        return f"- {location}：{self.description}；同义词：{'、'.join(self.synonyms) or '无'}"


DATA_DICTIONARY: tuple[DictionaryItem, ...] = (
    DictionaryItem("data", "fact_orders.amount", "订单实付金额，用于 GMV/销售额，不含退款和取消订单。", "fact_orders", "amount", synonyms=("销售额", "GMV", "成交额", "流水")),
    DictionaryItem("data", "fact_orders.order_date", "订单发生日期，用于时间范围、趋势和同比环比。", "fact_orders", "order_date", synonyms=("日期", "时间", "最近", "上个月")),
    DictionaryItem("data", "fact_orders.is_new_user", "订单是否为用户首单，用于新客/老客分析。", "fact_orders", "is_new_user", synonyms=("新客", "首单", "老客")),
    DictionaryItem("data", "fact_orders.channel", "订单来源渠道。", "fact_orders", "channel", synonyms=("渠道", "来源", "投放")),
    DictionaryItem("data", "fact_orders.quantity", "订单购买件数，用于销量。", "fact_orders", "quantity", synonyms=("销量", "件数", "卖了多少")),
    DictionaryItem("data", "dim_product.product_name", "商品展示名称。", "dim_product", "product_name", synonyms=("商品", "货品", "SKU")),
    DictionaryItem("data", "dim_product.category", "商品所属类目。", "dim_product", "category", synonyms=("类目", "品类")),
    DictionaryItem("data", "dim_product.stock", "当前库存数量，可用于识别滞销和计算基线补货建议。", "dim_product", "stock", synonyms=("库存", "存货", "滞销", "备货", "补货")),
    DictionaryItem("data", "dim_region.area", "大区名称，如华东、华南、华北。", "dim_region", "area", synonyms=("地区", "区域", "大区")),
    DictionaryItem("data", "dim_user.vip_level", "用户会员等级。", "dim_user", "vip_level", synonyms=("会员", "等级")),
)


METRIC_DICTIONARY: tuple[DictionaryItem, ...] = (
    DictionaryItem("metric", "销售额/GMV", "订单实付金额合计。", expression_sql="SUM(fact_orders.amount)", filter_sql="fact_orders.status = 'paid'", synonyms=("销售额", "GMV", "成交额")),
    DictionaryItem("metric", "订单量", "去重后的已支付订单数。", expression_sql="COUNT(DISTINCT fact_orders.order_id)", filter_sql="fact_orders.status = 'paid'", synonyms=("订单", "单量")),
    DictionaryItem("metric", "销量", "已支付订单的购买件数合计。", expression_sql="SUM(fact_orders.quantity)", filter_sql="fact_orders.status = 'paid'", synonyms=("销量", "件数")),
    DictionaryItem("metric", "客单价", "销售额除以订单量。", expression_sql="SUM(fact_orders.amount) / NULLIF(COUNT(DISTINCT fact_orders.order_id), 0)", filter_sql="fact_orders.status = 'paid'", synonyms=("客单价", "平均订单金额", "AOV")),
    DictionaryItem("metric", "复购率", "统计周期内 paid 订单数大于等于 2 的用户占比。", expression_sql="COUNT(*) FILTER (WHERE paid_orders >= 2) / NULLIF(COUNT(*), 0)", filter_sql="每个 user_id 聚合且订单 status = 'paid'", synonyms=("复购", "老客复购")),
    DictionaryItem("metric", "新客订单", "用户首单产生的已支付订单数。", expression_sql="COUNT(DISTINCT fact_orders.order_id)", filter_sql="fact_orders.status = 'paid' AND fact_orders.is_new_user = true", synonyms=("新客", "首单")),
)


def tokenize(text: str) -> set[str]:
    # Chinese characters are meaningful retrieval units; words cover SQL and
    # English questions.  Keeping this deterministic avoids an embedding API
    # call for every request when running the demo locally.
    lowered = text.lower()
    chars = {char for char in lowered if "\u4e00" <= char <= "\u9fff"}
    words = set(re.findall(r"[a-z][a-z0-9_]+", lowered))
    return chars | words


class DictionaryRetriever:
    def __init__(self, items: Iterable[DictionaryItem] | None = None) -> None:
        self.items = tuple(items or (*DATA_DICTIONARY, *METRIC_DICTIONARY))

    def retrieve(self, question: str, top_k: int = 8) -> list[DictionaryItem]:
        query_tokens = tokenize(question)
        scored: list[tuple[float, int, DictionaryItem]] = []
        for index, item in enumerate(self.items):
            searchable = tokenize(" ".join((item.name, item.description, *item.synonyms)))
            overlap = len(query_tokens & searchable)
            # Keep useful domain defaults available for short/ambiguous queries.
            prior = 0.15 if item.kind == "metric" else 0.0
            scored.append((overlap + prior, -index, item))
        scored.sort(reverse=True, key=lambda entry: (entry[0], entry[1]))
        return [item for score, _, item in scored[:top_k] if score > 0] or list(self.items[:top_k])

    def build_context(self, question: str, top_k: int = 8) -> tuple[str, str, list[str]]:
        items = self.retrieve(question, top_k=top_k)
        data_items = [item for item in items if item.kind == "data"]
        metric_items = [item for item in items if item.kind == "metric"]
        schema = "\n".join(item.render() for item in data_items)
        metrics = "\n".join(item.render() for item in metric_items)
        return schema, metrics, [item.name for item in items]

    @staticmethod
    def search_pgvector(
        query_embedding: list[float],
        *,
        kind: str = "metric",
        top_k: int = 5,
        settings: Settings | None = None,
    ) -> list[dict[str, Any]]:
        """Search persisted dictionary embeddings using pgvector cosine distance.

        Embedding generation is intentionally left to the configured model
        provider.  A null embedding is ignored, so seeded lexical records remain
        usable while a project is bootstrapped.
        """
        runtime_settings = settings or get_settings()
        table = "metric_dictionary" if kind == "metric" else "data_dictionary"
        vector = "[" + ",".join(str(float(value)) for value in query_embedding) + "]"
        columns = "metric_name, display_name, description, expression_sql, filter_sql, synonyms" if kind == "metric" else "table_name, column_name, description, synonyms"
        sql = f"SELECT {columns}, embedding <=> %s::vector AS distance FROM {table} WHERE embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT %s"
        with psycopg.connect(runtime_settings.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (vector, vector, top_k))
                return [dict(row) for row in cursor.fetchall()]


DEFAULT_RETRIEVER = DictionaryRetriever()


def retrieve_rag_context(question: str, top_k: int = 8) -> tuple[str, str, list[str]]:
    return DEFAULT_RETRIEVER.build_context(question, top_k=top_k)
