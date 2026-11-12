"""Single product version and immutable per-release build metadata."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import sys

__version__ = '0.4.0'


def build_info() -> dict:
    info = dict(version=__version__, build_number=0, built_at=None,
                source_revision=None, channel='development', architecture=platform.machine())
    if getattr(sys, 'frozen', False):
        path = Path(sys._MEIPASS) / 'assets' / 'build-info.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('version') != __version__ or type(payload.get('build_number')) is not int:
            raise ValueError('Application build metadata does not match its version')
        info.update({key: payload[key] for key in info if key in payload})
    return info


def version_text() -> str:
    info = build_info()
    channel = {'development': '开发运行', 'preview': '开发预览', 'stable': '正式版'}.get(info['channel'], info['channel'])
    return '\n'.join((
        '声笺 · Iris Caption',
        f"版本：{info['version']}（Build {info['build_number']}）",
        f"构建日期：{info['built_at'] or '源码开发运行'}",
        f"发布渠道：{channel}",
        f"架构：{info['architecture']}",
        f"源码标识：{info['source_revision'] or '未记录'}",
    ))
