"""终端、文件、飞书交付；状态持久化避免重复通知。"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from iris.core.exceptions import IrisRuntimeError
from iris.core.locks import FileLock
from iris.intelligence.storage import SnapshotStore
from iris.utils.shared import atomic_write_text

from .detector import Signal
from .history import signal_key
from .text import clip, compact_source, day_label

_KIND_ORDER = (
    'KR_AT_RISK', 'KR_EVIDENCE_GAP', 'DECISION_REVIEW_DUE', 'OPEN_DECISION_STALE',
    'SUPPLY_GAP', 'WEEKLY_MISSING', 'PROJECT_STALLED',
    'STALE_WIKI_ACTIVE', 'UNDOCUMENTED_TOPIC', 'NEW_PERSON_NO_PAGE', 'ORPHAN_CONCEPT',
)
_KIND_LABELS = {
    'KR_AT_RISK': '🚩 KR 风险',
    'KR_EVIDENCE_GAP': '📊 OKR 证据缺口',
    'DECISION_REVIEW_DUE': '⏳ 决策复盘',
    'OPEN_DECISION_STALE': '⏳ 决策停滞',
    'SUPPLY_GAP': '📭 栏目断供',
    'WEEKLY_MISSING': '📝 周报缺失',
    'PROJECT_STALLED': '🧱 项目停滞',
    'STALE_WIKI_ACTIVE': '📄 Wiki 脱节',
    'UNDOCUMENTED_TOPIC': '💬 未落文档话题',
    'NEW_PERSON_NO_PAGE': '👤 人物页缺失',
    'ORPHAN_CONCEPT': '🧩 概念页缺失',
}
_MAX_PER_SECTION = 3
_DECISION_ID_KINDS = {'DECISION_REVIEW_DUE', 'OPEN_DECISION_STALE'}


def build_title(day: date) -> str:
    """飞书 post 标题（--markdown 无法设置 title，故走 --content）。"""
    return f'Iris 日报 · {day_label(day)}'


def render(signals: list[Signal], pending: list[dict], *, day: date | None = None,
           annotations: dict[str, dict] | None = None,
           maintenance: dict[str, Any] | None = None) -> str:
    """生成飞书通知正文：TL;DR → 分区信号 → 决策待审 → 维护回执。"""
    day = day or date.today()
    annotations = annotations or {}
    pending = list(pending or [])
    active = [row for row in pending if not row.get('_orphan')]
    orphan_count = len(pending) - len(active)
    ai_approved = sum(1 for row in pending if row.get('_ai_approved'))
    lines = [_header_line(signals, pending, annotations, ai_approved)]
    lines.extend(_render_signal_sections(signals, annotations))
    lines.extend(_render_pending_section(active, pending, ai_approved, orphan_count))
    lines.extend(_render_maintenance(maintenance))
    return '\n'.join(lines)


def _header_line(signals: list[Signal], pending: list[dict],
                 annotations: dict[str, dict], ai_approved: int) -> str:
    if signals:
        head = f'**{len(signals)} 条信号'
        if annotations:
            new_count = sum(1 for s in signals if annotations.get(signal_key(s), {}).get('new'))
            head += f'（新增 {new_count} / 持续 {len(signals) - new_count}）'
        head += '**'
    else:
        head = '**今日暂无信号**'
    if pending:
        head += f' ｜ 待审 {len(pending)}'
        if ai_approved:
            head += f'（AI 已通过 {ai_approved}）'
    return head


def _render_signal_sections(signals: list[Signal],
                            annotations: dict[str, dict]) -> list[str]:
    if not signals:
        return []
    groups: dict[str, list[Signal]] = {}
    for signal in signals:
        groups.setdefault(signal.kind, []).append(signal)
    order = [k for k in _KIND_ORDER if k in groups]
    order += [k for k in groups if k not in _KIND_ORDER]
    lines: list[str] = []
    for kind in order:
        items = groups[kind]
        lines.extend(['', f'**{_KIND_LABELS.get(kind, kind)}（{len(items)}）**'])
        for signal in items[:_MAX_PER_SECTION]:
            lines.extend(_render_signal(signal, annotations.get(signal_key(signal))))
        if len(items) > _MAX_PER_SECTION:
            lines.append(f'- 其余 {len(items) - _MAX_PER_SECTION} 条同类信号见 CLI')
    return lines


def _render_signal(signal: Signal, annotation: dict | None) -> list[str]:
    lines = [f'- **{clip(signal.title, 60)}**']
    meta: list[str] = []
    if signal.detail:
        meta.append(clip(signal.detail, 46))
    sources = _display_sources(signal)
    if sources:
        shown = '、'.join(sources[:2])
        if len(sources) > 2:
            shown += f' 等 {len(sources)} 篇'
        meta.append(shown)
    if annotation:
        meta.append('新增' if annotation.get('new') else f'持续 {annotation.get("days", 1)} 天')
    if signal.action:
        meta.append(f'→ {signal.action}')
    if meta:
        lines.append('  ' + ' · '.join(meta))
    return lines


def _display_sources(signal: Signal) -> list[str]:
    if signal.kind in _DECISION_ID_KINDS:
        return []
    if signal.sources:
        return list(signal.sources)
    return [compact_source(path) for path in signal.evidence if path]


def _render_pending_section(active: list[dict], all_rows: list[dict],
                            ai_approved: int, orphan_count: int) -> list[str]:
    lines = ['', f'**⏳ 决策待审（{len(all_rows)}）**']
    if not all_rows:
        lines[-1] = '**⏳ 决策待审**：无'
        return lines
    if ai_approved:
        lines.append(f'AI 已通过 {ai_approved} 条，可优先确认')
    if orphan_count:
        lines.append(f'⚠️ {orphan_count} 条来源文档已不存在，建议归档')
    if not active:
        lines.append('其余候选来源均缺失，见 CLI 处理')
        return lines
    ordered = sorted(active, key=lambda row: (
        not row.get('_ai_approved'), -int(row.get('_pending_days', 0) or 0)))
    shown = _select_pending_rows(ordered)
    for row in shown:
        decision = row.get('decision', {}) or {}
        candidate_id = str(row.get('candidate_id', ''))
        short_id = candidate_id[:11] if candidate_id.startswith('CAN-') else candidate_id[:8]
        title = clip(str(decision.get('title', '')), 32)
        meta = []
        days = int(row.get('_pending_days', 0) or 0)
        if days > 0:
            meta.append(f'挂起 {days} 天')
        missing = int(row.get('_missing_sources', 0) or 0)
        if missing:
            meta.append(f'缺 {missing} 个来源')
        if row.get('_ai_approved'):
            meta.append('AI 已通过')
        suffix = f' · {" · ".join(meta)}' if meta else ''
        lines.append(f'- `{short_id}` {title}{suffix}')
        reason = clip(str(row.get('_reason', '')), 50)
        if reason:
            lines.append(f'  {reason}')
    remaining = len(active) - len(shown)
    if remaining > 0:
        lines.append(f'- 另 {remaining} 条见 `python3 scripts/run_cli.py decisions pending`')
    return lines


def _select_pending_rows(ordered: list[dict], limit: int = 5) -> list[dict]:
    """AI 已通过（快速确认）与普通待审混合展示，避免只看到一边。"""
    approved = [row for row in ordered if row.get('_ai_approved')]
    others = [row for row in ordered if not row.get('_ai_approved')]
    shown = approved[:3] + others[:2]
    if len(shown) < limit:
        shown += (approved[3:] + others[2:])[:limit - len(shown)]
    return shown[:limit]


def _render_maintenance(maintenance: dict[str, Any] | None) -> list[str]:
    if not maintenance:
        return []
    parts: list[str] = []
    if maintenance.get('text'):
        parts.append(str(maintenance['text']))
    if maintenance.get('documents') is not None:
        parts.append(f'文档 {int(maintenance["documents"]):,}')
    if maintenance.get('rebuilt') is not None:
        parts.append(f'重建 {int(maintenance["rebuilt"])}')
    if maintenance.get('wiki_updated') is not None:
        parts.append(f'Wiki 更新 {int(maintenance["wiki_updated"])}')
    if maintenance.get('graph'):
        parts.append(f'图谱 {maintenance["graph"]}')
    if maintenance.get('usage_calls') is not None:
        parts.append(f'用量 {int(maintenance["usage_calls"])} 次')
    lines: list[str] = []
    if parts:
        lines.extend(['', '**🛠 维护回执**', ' · '.join(parts)])
    for issue in maintenance.get('issues', []) or []:
        lines.append(f'⚠️ {issue}')
    return lines


def send_lark(user_id, text, key, *, profile="", expected_app_id="", title=""):
    prefix = ['lark-cli'] + (['--profile', profile] if profile else [])
    if expected_app_id:
        identity = subprocess.run(prefix + ['whoami', '--as', 'bot'],
                                  capture_output=True, text=True, timeout=15, check=False)
        try:
            payload = json.loads(identity.stdout)
        except ValueError as exc:
            raise IrisRuntimeError('无法解析 lark-cli whoami 输出，拒绝发送') from exc
        if identity.returncode or payload.get('appId') != expected_app_id:
            raise IrisRuntimeError('当前机器人应用与配置的 Iris 应用不一致，拒绝发送')
    if title:
        content = json.dumps({'zh_cn': {'title': title, 'content': [[{'tag': 'md', 'text': text}]]}},
                             ensure_ascii=False)
        args = prefix + ['im', '+messages-send', '--as', 'bot', '--user-id', user_id,
                         '--msg-type', 'post', '--content', content, '--idempotency-key', key]
    else:
        args = prefix + ['im', '+messages-send', '--as', 'bot', '--user-id', user_id,
                         '--markdown', text, '--idempotency-key', key]
    result = subprocess.run(args, capture_output=True, text=True, timeout=45, check=False)
    if result.returncode:
        raise IrisRuntimeError('飞书发送失败：' + result.stderr[:1200])
    payload = json.loads(result.stdout)
    if payload.get('ok') is not True or payload.get('identity') != 'bot':
        raise IrisRuntimeError('飞书未确认机器人发送成功')
    return payload.get('data', {})


class SignalDelivery:
    def __init__(self, root, sender=send_lark):
        self.root, self.sender = root, sender
        self.store = SnapshotStore(root / 'delivery')

    def deliver(self, text, *, day, user_id='', dry_run=False, title='', policy='daily'):
        print(text)
        digest = hashlib.sha256((day + user_id + text).encode()).hexdigest()
        # 存储 key 与幂等键统一使用同一截断值，避免两者不一致。
        path = self.root / f'{day}-{digest[:12]}.md'
        atomic_write_text(path, text)
        if dry_run:
            return {'file': str(path), 'status': 'dry_run'}
        if not user_id:
            return {'file': str(path), 'status': 'not_configured',
                    'reason': '未配置飞书收件人（feishu_user_id）'}
        policy = policy if policy in {'daily', 'every_run'} else 'daily'
        key = self._delivery_key(day, user_id, digest, policy)
        # 发送全过程用独立锁，避免两个 daily-start 同时投递。
        with FileLock(self.root / 'send'):
            records = self.store.read().get('records', {})
            if policy == 'daily':
                prior = self._find_day_record(records, day)
                if prior.get('status') in {'sent', 'sending', 'uncertain'}:
                    prior_status = str(prior.get('status'))
                    reason = ('当日已发送，未重复推送' if prior_status == 'sent'
                              else f'当日已有投递记录（{prior_status}），未重复推送')
                    return {'file': str(path), 'status': prior_status, 'duplicate': True,
                            'reason': reason, 'previous_file': prior.get('file', '')}
            old = records.get(key, {})
            if old.get('status') in {'sent', 'sending', 'uncertain'}:
                return {'file': str(path), 'status': old['status'], 'duplicate': True}
            record = {'status': 'sending', 'file': str(path), 'day': day,
                      'digest': digest, 'title': title, 'user_id': user_id}
            self._record(key, record)
            try:
                receipt = self.sender(user_id, text, key, title=title)
            except (OSError, subprocess.SubprocessError, ValueError, IrisRuntimeError) as exc:
                # 网络超时可能已送达，不盲目重发；记录不确定状态供人工核验。
                self._record(key, {**record, 'status': 'uncertain', 'error': str(exc)})
                return {'file': str(path), 'status': 'uncertain', 'reason': str(exc)}
            self._record(key, {**record, 'status': 'sent', 'receipt': receipt})
            return {'file': str(path), 'status': 'sent'}

    @staticmethod
    def _delivery_key(day: str, user_id: str, digest: str, policy: str) -> str:
        if policy == 'daily':
            return hashlib.sha256(f'daily|{day}|{user_id}'.encode()).hexdigest()[:40]
        return digest[:40]

    @staticmethod
    def _find_day_record(records: dict, day: str) -> dict:
        """找出当天已有的投递记录（兼容旧版只按内容哈希存、无 day 字段的记录）。"""
        matches = []
        for value in records.values():
            if not isinstance(value, dict):
                continue
            file_name = Path(str(value.get('file', ''))).name
            if value.get('day') == day or file_name.startswith(f'{day}-'):
                matches.append(value)
        for status in ('sent', 'sending', 'uncertain'):
            for row in matches:
                if row.get('status') == status:
                    return row
        return matches[0] if matches else {}

    def _record(self, key, value):
        self.store.change(lambda data: data['records'].__setitem__(key, value))
