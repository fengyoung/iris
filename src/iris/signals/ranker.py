"""稳定排序、去重与 kind 配额。"""
from __future__ import annotations

from collections import Counter

from .detector import Signal


def rank(signals: list[Signal], limit: int = 5, per_kind: int = 2) -> list[Signal]:
    """按紧急度排序，同时保证信号类型多样。

    旧实现直接取 urgency 前 5：只要有 5 条 KR_AT_RISK（urgency 7），
    Wiki 脱节、周报断档、栏目断供等信号永远进不了通知。现在先按类型配额
    各取至多 ``per_kind`` 条，再按剩余紧急度补满，避免单一类型霸榜。
    """
    unique: dict[tuple[str, str], Signal] = {}
    for signal in signals:
        key = (signal.kind, signal.title)
        old = unique.get(key)
        if old is None or (signal.urgency, signal.impact) > (old.urgency, old.impact):
            unique[key] = signal
    ordered = sorted(unique.items(), key=lambda pair: (
        -pair[1].urgency, -pair[1].impact, pair[1].kind, pair[1].title))
    picked: list[Signal] = []
    picked_keys: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    for key, signal in ordered:
        if len(picked) >= limit:
            break
        if counts[signal.kind] < per_kind:
            picked.append(signal)
            picked_keys.add(key)
            counts[signal.kind] += 1
    for key, signal in ordered:
        if len(picked) >= limit:
            break
        if key not in picked_keys:
            picked.append(signal)
            picked_keys.add(key)
    return sorted(picked, key=lambda s: (-s.urgency, -s.impact, s.kind, s.title))
