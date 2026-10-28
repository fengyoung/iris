"""信号跨日新鲜度：记录首见/最近推送，避免同一批信号每天重复轰炸。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path

from iris.core.locks import FileLock
from iris.utils.shared import atomic_write_json

from .detector import Signal

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_RETAIN_DAYS = 120


def signal_key(signal: Signal) -> str:
    """信号稳定指纹：优先用 detector 给出的 stable_key。"""
    basis = signal.stable_key or (
        f'{signal.kind}|{signal.title}|{signal.evidence[0] if signal.evidence else ""}')
    normalized = re.sub(r'\s+', '', basis)
    return hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:20]


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat((value or '')[:10])
    except (TypeError, ValueError):
        return None


class SignalHistory:
    """data/signals/history.json 的读写；损坏时按空历史处理。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / 'history.json'
        self.lock_path = self.root / 'history'

    def annotate(self, signals: list[Signal], *, today: date) -> dict[str, dict]:
        rows = self._load().get('signals', {})
        result: dict[str, dict] = {}
        for signal in signals:
            key = signal_key(signal)
            row = rows.get(key) or {}
            first = _parse_day(str(row.get('first_seen', ''))) or today
            result[key] = {
                'new': first >= today,
                'days': max(1, (today - first).days + 1),
                'last_pushed': str(row.get('last_pushed', '')),
            }
        return result

    def record(self, signals: list[Signal], *, today: date, pushed: bool) -> None:
        try:
            with FileLock(self.lock_path):
                data = self._load()
                rows = data.setdefault('signals', {})
                for signal in signals:
                    key = signal_key(signal)
                    row = rows.setdefault(key, {})
                    row.setdefault('first_seen', today.isoformat())
                    row['last_seen'] = today.isoformat()
                    if pushed:
                        row['last_pushed'] = today.isoformat()
                cutoff = (today - timedelta(days=_RETAIN_DAYS)).isoformat()
                data['signals'] = {
                    key: row for key, row in rows.items()
                    if str(row.get('last_seen', '')) >= cutoff
                }
                data['schema_version'] = _SCHEMA_VERSION
                atomic_write_json(self.path, data)
        except Exception as exc:  # noqa: BLE001 - 历史写入失败不应阻断通知
            logger.warning('信号历史写入失败（不影响通知）: %s', exc)

    def _load(self) -> dict:
        if not self.path.exists():
            return {'schema_version': _SCHEMA_VERSION, 'signals': {}}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            logger.warning('信号历史读取失败，按空历史处理: %s', exc)
            return {'schema_version': _SCHEMA_VERSION, 'signals': {}}
        if not isinstance(data, dict) or not isinstance(data.get('signals', {}), dict):
            return {'schema_version': _SCHEMA_VERSION, 'signals': {}}
        data.setdefault('schema_version', _SCHEMA_VERSION)
        return data
