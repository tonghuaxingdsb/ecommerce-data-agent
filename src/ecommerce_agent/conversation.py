from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Iterable


@dataclass(frozen=True)
class ConversationTurn:
    question: str
    answer: str = ""
    intent: str = ""


def resolve_follow_up(question: str, history: Iterable[ConversationTurn | dict[str, str]] | None) -> str:
    turns = list(history or [])
    if not turns:
        return question
    normalized = question.strip().lower()
    follow_up_markers = ("这个", "刚才", "上一", "继续", "它", "该", "呢", "那个", "同比", "环比")
    if not any(marker in normalized for marker in follow_up_markers):
        return question
    previous = turns[-1]
    previous_question = previous.question if isinstance(previous, ConversationTurn) else previous.get("question", "")
    return f"基于上一轮问题「{previous_question}」，当前追问：「{question}」"


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
