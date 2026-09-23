"""本地时间、配置与安全来源快照。"""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from iris.core.exceptions import IrisValueError
from iris.wiki._sensitive import is_sensitive_path, is_sensitive_title
from iris.wiki.searcher import parse_frontmatter


def settings(bundle) -> dict:
    cfg = dict(bundle.app.get('intelligence', {}) or {})
    threshold = cfg.get('okr_threshold', 0.65)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise IrisValueError('okr_threshold 必须在 0–1 之间')
    cfg.setdefault('okr_threshold', 0.65)
    cfg.setdefault('max_documents', 100)
    cfg.setdefault('max_prompt_chars', 48000)
    for key in ('max_documents', 'max_prompt_chars'):
        if type(cfg[key]) is not int or cfg[key] <= 0:
            raise IrisValueError(f'{key} 必须为正整数')
    return cfg


def data_root(bundle) -> Path:
    return bundle.root / bundle.app.get('paths', {}).get('data_dir', 'data')


def source_root(bundle) -> Path:
    ds = bundle.data_source
    sources = ds.get('sources', {})
    default = ds.get('default_source', '')
    selected = sources.get(default) if default else None
    if selected and selected.get('path'):
        return Path(selected['path']).resolve()
    for item in sources.values():
        if item.get('enabled', True) and item.get('path'):
            return Path(item['path']).resolve()
    raise IrisValueError('未配置 SOURCE 数据源')


def local_today() -> date:
    return datetime.now().astimezone().date()


def document_date(path: Path, fields: dict) -> date:
    value = fields.get('date', '')
    match = re.search(r'(\d{4})-?(\d{2})-?(\d{2})', value or path.name)
    if match:
        try:
            return date(*map(int, match.groups()))
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().date()


@dataclass
class SourceDoc:
    path: str
    title: str
    text: str
    date: date
    fingerprint: str

    def evidence(self, limit=1800) -> dict:
        return {'path': self.path, 'title': self.title, 'date': self.date.isoformat(), 'snippet': self.text[:limit]}


def read_document(root: Path, path: Path) -> SourceDoc | None:
    if not path.resolve().is_relative_to(root.resolve()) or is_sensitive_path(path.name):
        return None
    text = path.read_text(encoding='utf-8')
    fields, body = parse_frontmatter(text)
    title = fields.get('title', path.stem)
    if is_sensitive_title(title):
        return None
    return SourceDoc(path.relative_to(root).as_posix(), title, body, document_date(path, fields),
                     hashlib.sha256(text.encode()).hexdigest())


def documents(root: Path) -> list[SourceDoc]:
    rows = []
    for path in sorted(root.rglob('*.md')):
        doc = read_document(root, path)
        if doc:
            rows.append(doc)
    return rows


def parse_json(text: str):
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    return json.loads(cleaned)


def generate_json(llm, prompt: str, task: str, role='base_model'):
    response = llm.generate(prompt, route_context={'task_type': task, 'input_type': 'text',
                                                  'user_selected_role': role},
                            temperature=0, max_retries=0)
    return parse_json(response.text)
