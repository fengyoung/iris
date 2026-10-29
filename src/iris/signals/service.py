"""每日信号汇总，独立来源失败不吞掉其它信号。"""
from __future__ import annotations

import logging
import os
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

from iris.analysis.reminders import ReminderEngine
from iris.decisions.store import DecisionStore, pending_reason
from iris.intelligence.context import data_root, documents, local_today, settings, source_root

from .delivery import SignalDelivery, build_title, render, send_lark
from .detector import Signal, SignalDetector, reminder_signals
from .feed_cache import recent_topics
from .history import SignalHistory
from .ranker import rank

logger = logging.getLogger(__name__)


def run(bundle, *, dry_run=False, reminders: dict | None = None,
        maintenance: dict[str, Any] | None = None, notify: bool = True,
        force_notify: bool = False) -> dict:
    root = data_root(bundle)
    cfg = settings(bundle)
    day = local_today()
    src_root = source_root(bundle)
    docs = documents(src_root)
    store = DecisionStore(root / 'decisions')
    signals, errors = _collect_signals(bundle, root, docs, store)
    signals += _reminder_signals(bundle, reminders, errors)
    ranked = rank(signals)
    history = SignalHistory(root / 'signals')
    annotations = _safe_annotate(history, ranked, day)
    pending = _annotate_pending(store.pending(), src_root, day)
    summary = render(ranked, pending, day=day, annotations=annotations, maintenance=maintenance)
    if errors:
        summary += '\n\n检测不完整：\n' + '\n'.join(errors)
        for error in errors:
            logger.warning(error)
    delivery = _deliver(root, cfg, summary, day=day, dry_run=dry_run, notify=notify,
                        force_notify=force_notify)
    # 只有真正发出去（或当日已有发送记录）才推进历史；--no-notify / 未配置收件人
    # 不应让信号在第二天被误标成「持续」。
    if notify and not dry_run and delivery.get('status') == 'sent':
        _safe_record(history, ranked, day, pushed=not delivery.get('duplicate'), dry_run=False)
    return {'signals': [s.to_dict() for s in ranked], 'counts': _counts(ranked, pending),
            'delivery': delivery, 'errors': errors}


def _collect_signals(bundle, root: Path, docs, store: DecisionStore) -> tuple[list[Signal], list[str]]:
    detector = SignalDetector()
    errors: list[str] = []
    signals = detector.decisions(store.list(), docs)
    signals += detector.undocumented(recent_topics(root, local_today()), docs)
    wiki = bundle.wiki.get('wiki_root') if bundle.wiki else None
    if wiki:
        from iris.wiki.freshness import WikiFreshnessChecker
        try:
            for row in WikiFreshnessChecker(bundle).run(docs):
                signals.append(Signal('STALE_WIKI_ACTIVE', row['page'] + ' 与近期文档脱节',
                                      row['sources'], '核对并更新 Wiki', 6, 3,
                                      stable_key=f'STALE_WIKI_ACTIVE|{row["page"]}'))
        except Exception as exc:  # noqa: BLE001 - 独立检测器故障显式汇报
            errors.append('Wiki 鲜度检测失败：' + str(exc))
        titles = {p.stem for p in Path(wiki).rglob('*.md')}
        signals += detector.missing_entities(docs, titles)
    try:
        from iris.okr_evidence.service import evidence_context
        krs, evidence = evidence_context(bundle)
        signals += detector.kr_signals(krs, evidence)
    except Exception as exc:  # noqa: BLE001 - 独立检测器故障显式汇报
        errors.append('OKR 信号检测失败：' + str(exc))
    return signals, errors


def _reminder_signals(bundle, reminders: dict | None, errors: list[str]) -> list[Signal]:
    payload = reminders
    if payload is None:
        try:
            payload = ReminderEngine(bundle).collect()
        except Exception as exc:  # noqa: BLE001 - 独立检测器故障显式汇报
            errors.append('主动提醒采集失败：' + str(exc))
            return []
    if isinstance(payload, dict) and payload.get('status') == 'error':
        errors.append('主动提醒采集失败：' + str(payload.get('reason', '')))
    return reminder_signals(payload if isinstance(payload, dict) else {})


def _annotate_pending(rows: list[dict], src_root: Path, day: date) -> list[dict]:
    annotated: list[dict] = []
    for row in rows:
        item = dict(row)
        decision = item.get('decision', {}) or {}
        details = decision.get('review_details', {}) or {}
        review = details.get('review', {}) if isinstance(details, dict) else {}
        item['_reason'] = pending_reason(row)
        item['_pending_days'] = _pending_days(decision, day)
        item['_ai_approved'] = isinstance(review, dict) and review.get('verdict') == 'approved'
        paths = [source.get('path', '') for source in decision.get('sources', [])
                 if isinstance(source, dict) and source.get('path')]
        missing = [path for path in paths if not (src_root / path).exists()]
        item['_missing_sources'] = len(missing)
        item['_orphan'] = bool(paths) and len(missing) == len(paths)
        annotated.append(item)
    return annotated


def _pending_days(decision: dict, today: date) -> int:
    for key in ('created_at', 'decided_at'):
        value = str(decision.get(key, '') or '')[:10]
        try:
            return max(0, (today - date.fromisoformat(value)).days)
        except (TypeError, ValueError):
            continue
    return 0


def _safe_annotate(history: SignalHistory, ranked: list[Signal], day: date) -> dict[str, dict]:
    try:
        return history.annotate(ranked, today=day)
    except Exception as exc:  # noqa: BLE001 - 历史读取失败不应阻断通知
        logger.warning('信号历史读取失败: %s', exc)
        return {}


def _safe_record(history: SignalHistory, ranked: list[Signal], day: date,
                 *, pushed: bool, dry_run: bool) -> None:
    if dry_run:
        return
    history.record(ranked, today=day, pushed=pushed)


def _deliver(root: Path, cfg: dict, summary: str, *, day: date,
             dry_run: bool, notify: bool, force_notify: bool = False) -> dict:
    feishu_app_id = cfg.get('feishu_app_id', '')
    if not feishu_app_id:
        logger.warning('feishu_app_id 未配置，将跳过飞书机器人应用身份校验，无法保证以 Iris 身份发送')
    sender = partial(send_lark, profile=cfg.get('feishu_profile', ''),
                     expected_app_id=feishu_app_id)
    delivery = SignalDelivery(root / 'signals', sender)
    title = build_title(day)
    if notify:
        user_id = cfg.get('feishu_user_id', '') or os.environ.get('IRIS_BOT_USER_ID', '')
        return delivery.deliver(summary, day=day.isoformat(), user_id=user_id,
                                dry_run=dry_run, title=title,
                                policy=cfg.get('notify_policy', 'daily'),
                                force=force_notify)
    result = delivery.deliver(summary, day=day.isoformat(), user_id='',
                              dry_run=True, title=title,
                              policy=cfg.get('notify_policy', 'daily'))
    result['status'] = 'disabled'
    result['reason'] = '飞书通知已关闭'
    return result


def _counts(ranked: list[Signal], pending: list[dict]) -> dict:
    return {
        'signals': len(ranked),
        'pending': len(pending),
        'ai_approved': sum(1 for row in pending if row.get('_ai_approved')),
        'orphan_pending': sum(1 for row in pending if row.get('_orphan')),
    }
