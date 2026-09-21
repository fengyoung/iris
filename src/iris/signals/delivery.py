"""终端、文件、飞书交付；状态持久化避免重复通知。"""
import hashlib
import json
import subprocess
from iris.core.exceptions import IrisRuntimeError
from iris.core.locks import FileLock
from iris.intelligence.storage import SnapshotStore
from iris.utils.shared import atomic_write_text


def render(signals, pending):
    lines = [f'# 今日信号摘要（{len(signals)} 条）', '']
    for signal in signals:
        lines.extend([f'## [{signal.kind}] {signal.title}', '来源：' + '、'.join(signal.evidence),
                      '建议：' + signal.action, ''])
    lines.append(f'待审核决策：{len(pending)} 条')
    for row in pending[:5]:
        decision = row['decision']
        lines.extend([f"- {row['candidate_id']}：{decision['outcome']}",
                      '  待确认原因：' + decision['review_details'].get('reason', '')])
        lines.extend('  原文：' + s.get('quote', '') for s in decision['sources'][:2])
    return '\n'.join(lines)


def send_lark(user_id, text, key, *, profile="", expected_app_id=""):
    prefix = ['lark-cli'] + (['--profile', profile] if profile else [])
    if expected_app_id:
        identity = subprocess.run(prefix + ['whoami', '--as', 'bot'], capture_output=True, text=True, timeout=15, check=False)
        if identity.returncode or json.loads(identity.stdout).get('appId') != expected_app_id:
            raise IrisRuntimeError('当前机器人应用与配置的 Iris 应用不一致，拒绝发送')
    result = subprocess.run(prefix + ['im', '+messages-send', '--as', 'bot', '--user-id', user_id,
                             '--markdown', text, '--idempotency-key', key], capture_output=True,
                            text=True, timeout=45, check=False)
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

    def deliver(self, text, *, day, user_id='', dry_run=False):
        print(text)
        digest = hashlib.sha256((day + user_id + text).encode()).hexdigest()
        path = self.root / f'{day}-{digest[:12]}.md'
        atomic_write_text(path, text)
        if dry_run:
            return {'file': str(path), 'status': 'dry_run'}
        if not user_id:
            return {'file': str(path), 'status': 'not_configured', 'reason': '未配置冯扬的飞书 open_id'}
        # 发送全过程用独立锁，避免两个 daily-start 同时投递。
        with FileLock(self.root / 'send'):
            old = self.store.read().get('records', {}).get(digest, {})
            if old.get('status') in {'sent', 'sending', 'uncertain'}:
                return {'file': str(path), 'status': old['status'], 'duplicate': True}
            self._record(digest, {'status': 'sending', 'file': str(path)})
            try:
                receipt = self.sender(user_id, text, digest[:40])
            except (OSError, subprocess.SubprocessError, ValueError, IrisRuntimeError) as exc:
                # 网络超时可能已送达，不盲目重发；记录不确定状态供人工核验。
                self._record(digest, {'status': 'uncertain', 'error': str(exc), 'file': str(path)})
                return {'file': str(path), 'status': 'uncertain', 'reason': str(exc)}
            self._record(digest, {'status': 'sent', 'receipt': receipt, 'file': str(path)})
            return {'file': str(path), 'status': 'sent'}

    def _record(self, key, value):
        self.store.change(lambda data: data['records'].__setitem__(key, value))
