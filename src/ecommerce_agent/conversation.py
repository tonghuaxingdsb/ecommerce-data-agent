from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Iterable

from ecommerce_agent.capabilities import requires_fresh_context


@dataclass(frozen=True)
class ConversationTurn:
    question: str
    answer: str = ""
    intent: str = ""
    query_spec: dict[str, Any] = field(default_factory=dict)


def resolve_follow_up(question: str, history: Iterable[ConversationTurn | dict[str, Any]] | None) -> str:
    turns = list(history or [])
    if not turns or not is_follow_up_question(question, turns):
        return question
    previous = turns[-1]
    previous_question = previous.question if isinstance(previous, ConversationTurn) else previous.get("question", "")
    return f"这是同一会话中的追问。上一轮问题：「{previous_question}」。当前追问：「{question}」。未重新指定的指标、时间和筛选条件沿用上一轮。"


def inherited_query_spec(
    question: str,
    history: Iterable[ConversationTurn | dict[str, Any]] | None,
) -> dict[str, Any]:
    turns = list(history or [])
    if not turns or not is_follow_up_question(question, turns):
        return {}
    previous = turns[-1]
    value = previous.query_spec if isinstance(previous, ConversationTurn) else previous.get("query_spec", {})
    return dict(value) if isinstance(value, dict) else {}


def is_follow_up_question(
    question: str,
    history: Iterable[ConversationTurn | dict[str, Any]] | None,
) -> bool:
    if not list(history or []):
        return False
    if requires_fresh_context(question):
        return False
    text = question.strip().lower().replace(" ", "")
    direct_markers = (
        "这个",
        "这些",
        "刚才",
        "上一",
        "继续",
        "它",
        "该商品",
        "该类目",
        "该渠道",
        "该地区",
        "该指标",
        "该问题",
        "呢",
        "那个",
        "其中",
        "再看",
        "进一步",
    )
    if any(marker in text for marker in direct_markers):
        return True
    discourse_markers = ("贡献来源", "主要贡献", "增长贡献", "拉动", "驱动", "原因", "拆解", "细分")
    if any(marker in text for marker in discourse_markers):
        return True
    explicit_metrics = ("销售额", "gmv", "成交额", "金额", "订单量", "订单数", "销量", "件数", "客单价", "复购率", "复购", "转化率", "利润", "毛利", "新增用户", "注册用户", "退款订单", "会员等级分布", "卖得最差", "卖得最少", "卖得最好", "卖得最多")
    if any(metric in text for metric in explicit_metrics):
        return False
    dimensions = ("商品", "货品", "sku", "类目", "品类", "渠道", "地区", "区域", "会员等级")
    ranking_or_breakdown = ("top", "最高", "最低", "排名", "排行", "哪些", "什么", "哪个", "分别", "按")
    return any(dimension in text for dimension in dimensions) and any(marker in text for marker in ranking_or_breakdown)


def is_ambiguous_metric_question(question: str) -> bool:
    text = question.lower().replace(" ", "")
    vague = ("卖得怎么样", "卖得好不好", "表现怎么样", "情况怎么样", "经营得怎么样")
    explicit_metric = ("销售额", "gmv", "订单", "销量", "客单价", "复购", "库存", "转化", "金额")
    return any(token in text for token in vague) and not any(token in text for token in explicit_metric)


class ConversationStore:
    """Bounded in-memory conversation store for the demo API.

    It is deliberately replaceable: Phase 3 can move this interface to Redis
    or a database without changing the Agent graph contract.
    """

    def __init__(self, max_turns: int = 20) -> None:
        self._turns: dict[str, list[ConversationTurn]] = {}
        self._max_turns = max_turns
        self._lock = Lock()

    def get(self, conversation_id: str) -> list[ConversationTurn]:
        with self._lock:
            return list(self._turns.get(conversation_id, ()))

    def append(self, conversation_id: str, turn: ConversationTurn) -> None:
        with self._lock:
            turns = self._turns.setdefault(conversation_id, [])
            turns.append(turn)
            del turns[:-self._max_turns]
