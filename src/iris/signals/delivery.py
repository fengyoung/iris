"""终端、文件、飞书交付；状态持久化避免重复通知。"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime, timedelta
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
# 会阻止同一日期/同一内容重复投递的状态。
_BLOCKING_STATUSES = ('sent', 'sending', 'uncertain')
# 发送锁覆盖整个 send 过程：取得锁后仍看到 sending，说明上一次进程已中断。
# TTL 只是跨机器 / NFS 场景的保险，避免把真正在途的发送误判成残留。
_SENDING_TTL = timedelta(minutes=10)
_STALE_SENDING_REASON = '上次发送进程中断，结果未知，请人工核验'


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

    def deliver(self, text, *, day, user_id='', dry_run=False, title='',
                policy='daily', force: bool = False) -> dict:
        print(text)
        digest = hashlib.sha256((day + user_id + text).encode()).hexdigest()
        # 存储 key 与幂等键统一使用同一截断值，避免两者不一致。
        real_path = self.root / f'{day}-{digest[:12]}.md'
        preview_path = self.root / f'{day}-{digest[:12]}-preview.md'
        if dry_run:
            atomic_write_text(preview_path, text)
            return {'file': str(preview_path), 'status': 'dry_run'}
        if not user_id:
            atomic_write_text(preview_path, text)
            return {'file': str(preview_path), 'status': 'not_configured',
                    'reason': '未配置飞书收件人（feishu_user_id）'}
        policy = policy if policy in {'daily', 'every_run'} else 'daily'
        key = self._delivery_key(day, user_id, digest, policy)
        # 发送全过程用独立锁，避免两个 daily-start 同时投递。
        with FileLock(self.root / 'send'):
            records = self.store.read().get('records', {})
            if not force:
                duplicate = self._check_duplicates(records, day, key, policy)
                if duplicate:
                    return duplicate
            record = {'status': 'sending', 'file': str(real_path), 'day': day,
                      'digest': digest, 'title': title, 'user_id': user_id,
                      'started_at': datetime.now().astimezone().isoformat()}
            # 先落归档再记状态：崩溃只会留下孤立文件，不会造成重复发送。
            atomic_write_text(real_path, text)
            self._record(key, record)
            try:
                receipt = self.sender(user_id, text, key, title=title)
            except (OSError, subprocess.SubprocessError, ValueError, IrisRuntimeError) as exc:
                # 网络超时可能已送达，不盲目重发；记录不确定状态供人工核验。
                self._record(key, {**record, 'status': 'uncertain', 'error': str(exc)})
                return {'file': str(real_path), 'status': 'uncertain', 'reason': str(exc)}
            self._record(key, {**record, 'status': 'sent', 'receipt': receipt})
            return {'file': str(real_path), 'status': 'sent'}

    def _check_duplicates(self, records: dict, day: str, key: str, policy: str) -> dict | None:
        """返回幂等响应；发现发送进程残留时顺带把 sending 降级为 uncertain。"""
        if policy == 'daily':
            prior_key, prior = self._find_day_record(records, day)
            if prior.get('status') in _BLOCKING_STATUSES:
                return self._duplicate_response(prior_key, prior, scope='day')
        old = records.get(key, {})
        if old.get('status') in _BLOCKING_STATUSES:
            return self._duplicate_response(key, old, scope='content')
        return None

    def _duplicate_response(self, record_key: str | None, record: dict, *, scope: str) -> dict:
        status = str(record.get('status'))
        previous_file = str(record.get('file', '') or '')
        if status == 'sending' and self._sending_stale(record):
            reason = _STALE_SENDING_REASON
            if record_key:
                self._record(record_key, {**record, 'status': 'uncertain', 'error': reason})
            return {'file': previous_file, 'status': 'uncertain', 'duplicate': True,
                    'reason': reason, 'previous_file': previous_file}
        scope_label = '当日' if scope == 'day' else '该内容'
        if status == 'sent':
            reason = f'{scope_label}已发送，未重复推送'
        elif status == 'sending':
            reason = f'{scope_label}正在发送，未重复推送；超过 10 分钟无结果将转为不确定'
        else:
            reason = f'{scope_label}已有投递记录（uncertain），未重复推送'
        return {'file': previous_file, 'status': status, 'duplicate': True,
                'reason': reason, 'previous_file': previous_file}

    @staticmethod
    def _sending_stale(record: dict) -> bool:
        """sending 记录是否来自已中断的进程；缺时间戳/解析失败一律按残留处理。"""
        raw = str(record.get('started_at', '') or '')
        if not raw:
            return True
        try:
            started = datetime.fromisoformat(raw)
        except ValueError:
            return True
        if started.tzinfo is None:
            started = started.astimezone()
        return datetime.now().astimezone() - started >= _SENDING_TTL

    @staticmethod
    def _delivery_key(day: str, user_id: str, digest: str, policy: str) -> str:
        if policy == 'daily':
            return hashlib.sha256(f'daily|{day}|{user_id}'.encode()).hexdigest()[:40]
        return digest[:40]

    @staticmethod
    def _find_day_record(records: dict, day: str) -> tuple[str | None, dict]:
        """找出当天已有的投递记录（兼容旧版只按内容哈希存、无 day 字段的记录）。"""
        matches: list[tuple[str, dict]] = []
        for record_key, value in records.items():
            if not isinstance(value, dict):
                continue
            file_name = Path(str(value.get('file', ''))).name
            if value.get('day') == day or file_name.startswith(f'{day}-'):
                matches.append((record_key, value))
        for status in _BLOCKING_STATUSES:
            for record_key, row in matches:
                if row.get('status') == status:
                    return record_key, row
        return matches[0] if matches else (None, {})

    def _record(self, key, value):
        self.store.change(lambda data: data['records'].__setitem__(key, value))
