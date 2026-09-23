"""每日信号汇总，独立来源失败不吞掉其它信号。"""
import logging
import os
from pathlib import Path
from iris.decisions.store import DecisionStore
from iris.intelligence.context import data_root, source_root, documents, local_today, settings
from .detector import Signal, SignalDetector
from .ranker import rank
from .delivery import SignalDelivery, render, send_lark
from .feed_cache import recent_topics

logger = logging.getLogger(__name__)


def run(bundle, *, dry_run=False):
    root = data_root(bundle)
    cfg = settings(bundle)
    docs = documents(source_root(bundle))
    detector = SignalDetector()
    store = DecisionStore(root / 'decisions')
    signals = detector.decisions(store.list(), docs)
    signals += detector.undocumented(recent_topics(root, local_today()), docs)
    errors = []
    wiki = bundle.wiki.get('wiki_root') if bundle.wiki else None
    if wiki:
        from iris.wiki.freshness import WikiFreshnessChecker
        try:
            for row in WikiFreshnessChecker(bundle).run(docs):
                signals.append(Signal('STALE_WIKI_ACTIVE', row['page'] + ' 与近期文档脱节',
                                      row['sources'], '核对并更新 Wiki', 6, 3))
        except Exception as exc:
            # 独立检测器故障显式汇报，不能中断其它信号。
            errors.append('Wiki 鲜度检测失败：' + str(exc))
        titles = {p.stem for p in Path(wiki).rglob('*.md')}
        signals += detector.missing_entities(docs, titles)
    try:
        from iris.okr_evidence.service import evidence_context
        krs, evidence = evidence_context(bundle)
        signals += detector.kr_signals(krs, evidence)
    except Exception as exc:
        errors.append('OKR 信号检测失败：' + str(exc))
    ranked = rank(signals)
    summary = render(ranked, store.pending())
    if errors:
        summary += '\n\n检测不完整：\n' + '\n'.join(errors)
        for error in errors:
            logger.warning(error)
    from functools import partial
    feishu_app_id = cfg.get('feishu_app_id', '')
    if not feishu_app_id:
        logger.warning('feishu_app_id 未配置，将跳过飞书机器人应用身份校验，无法保证以 Iris 身份发送')
    sender = partial(send_lark, profile=cfg.get('feishu_profile', ''), expected_app_id=feishu_app_id)
    delivery = SignalDelivery(root / 'signals', sender).deliver(summary, day=local_today().isoformat(),
                     user_id=cfg.get('feishu_user_id', '') or os.environ.get('IRIS_BOT_USER_ID', ''), dry_run=dry_run)
    return {'signals': [s.to_dict() for s in ranked], 'delivery': delivery, 'errors': errors}
