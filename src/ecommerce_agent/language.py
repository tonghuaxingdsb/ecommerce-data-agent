from __future__ import annotations


def detect_response_language(text: str) -> str:
    return "zh" if any("\u4e00" <= char <= "\u9fff" for char in text) else "en"

