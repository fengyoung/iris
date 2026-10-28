"""信号通知的文本工具：来源压缩、截断、日期格式化。"""
from __future__ import annotations

import re
from datetime import date
from pathlib import PurePosixPath

_FILE_DATE_RE = re.compile(r'^(\d{8})[-_](.+)$')
_WEEKDAYS = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')


def short_date(value: str) -> str:
    """把 ``2026-10-17`` / ``20261017`` / ISO 时间戳统一成 ``10-17``。"""
    digits = re.sub(r'\D', '', value or '')
    if len(digits) >= 8:
        return f'{digits[4:6]}-{digits[6:8]}'
    return ''


def compact_source(path: str, doc_date: str = '') -> str:
    """把 SOURCE 相对路径压缩成「文档名 · MM-DD」，通知正文不再出现长目录。"""
    stem = PurePosixPath(path or '').stem
    name = stem
    date_text = short_date(doc_date)
    match = _FILE_DATE_RE.match(stem)
    if match:
        name = match.group(2)
        if not date_text:
            date_text = short_date(match.group(1))
    return f'{name} · {date_text}' if date_text else name


def clip(text: str, limit: int) -> str:
    """压缩空白并截断到 limit 字符；截断时保留一个省略号。"""
    text = re.sub(r'\s+', ' ', (text or '').strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip('，,；;。.、 ') + '…'


def day_label(day: date) -> str:
    """``2026-10-28`` → ``10月28日（周三）``。"""
    return f'{day.month}月{day.day}日（{_WEEKDAYS[day.weekday()]}）'
