"""ASR Prompt 版本管理 — 指纹计算、版本号升降、持久化。

支持 major / minor / patch 三级语义版本号。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

# 运行时导入（非 TYPE_CHECKING），因为函数体内需要构造 AsrPromptVersion 实例。
# term_extractor 的重导出在文件末尾，不会形成循环导入。
from ._types import AsrPromptVersion  # noqa: E402

if TYPE_CHECKING:
    from ..context_loader import WikiPageInfo

_VERSION_FILE = "asr_prompt_version.json"


def _now_iso() -> str:
    """当前时间的 ISO 8601 字符串（含时区）。"""
    return datetime.now(timezone.utc).isoformat()


def compute_fingerprint(pages: List[WikiPageInfo]) -> str:
    """计算 Wiki 页面内容指纹。

    对每页 (title, type, body[:500]) 拼接后计算 SHA-256，返回前 16 位 hex。
    使用 body[:500] 而非完整正文，避免微小文字改动频繁触发版本变化。
    """
    h = hashlib.sha256()
    for page in sorted(pages, key=lambda p: str(p.path)):
        snippet = f"{page.title}|{page.page_type}|{page.body[:500]}"
        h.update(snippet.encode("utf-8"))
    return h.hexdigest()[:16]


def load_version(data_dir: Path) -> Optional[AsrPromptVersion]:
    """加载版本文件。文件不存在或损坏时返回 None。"""
    path = data_dir / _VERSION_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AsrPromptVersion(
            version=data.get("version", "0.0.0"),
            generated_at=data.get("generated_at", ""),
            wiki_page_count=data.get("wiki_page_count", 0),
            term_count=data.get("term_count", 0),
            fingerprint=data.get("fingerprint", ""),
            prompt_text=data.get("prompt_text", ""),
        )
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def save_version(data_dir: Path, version: AsrPromptVersion) -> None:
    """持久化版本信息到 JSON 文件。使用 FileLock 保证并发安全。"""
    from iris.core.locks import FileLock

    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / _VERSION_FILE

    payload = {
        "version": version.version,
        "generated_at": version.generated_at,
        "wiki_page_count": version.wiki_page_count,
        "term_count": version.term_count,
        "fingerprint": version.fingerprint,
        "prompt_text": version.prompt_text,
    }

    with FileLock(str(path)):
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def bump_version(current: str, bump: str) -> str:
    """纯函数：递增三段式版本号。

    Args:
        current: 当前版本号，如 "1.0.0"
        bump: "major" | "minor" | "patch" | "auto"

    Returns:
        新版本号字符串
    """
    try:
        parts = [int(x) for x in current.split(".")]
        while len(parts) < 3:
            parts.append(0)
        major, minor, patch = parts[0], parts[1], parts[2]
    except (ValueError, TypeError):
        major, minor, patch = 0, 0, 0

    if bump == "major":
        return f"{major + 1}.0.0"
    elif bump == "minor":
        return f"{major}.{minor + 1}.0"
    else:  # patch 或 auto
        return f"{major}.{minor}.{patch + 1}"


def determine_new_version(
    pages: List[WikiPageInfo],
    data_dir: Path,
    bump: str = "auto",
) -> AsrPromptVersion:
    """综合判定新版本号。

    auto 模式：
    - 指纹无变化 → 返回旧版本（版本号不变）
    - 指纹有变化 → bump patch

    手动模式（major/minor/patch）：
    - 始终递增，忽略指纹

    Args:
        pages: 已加载的 Wiki 页面列表
        data_dir: 项目 data/ 目录
        bump: "auto" | "major" | "minor" | "patch"

    Returns:
        新版本信息（auto 且指纹不变时返回旧版本）
    """
    fingerprint = compute_fingerprint(pages)
    now = _now_iso()
    old = load_version(data_dir)

    if bump == "auto" and old and old.fingerprint == fingerprint:
        # 指纹不变，返回旧版本
        return old

    if old:
        new_ver = bump_version(old.version, bump)
    else:
        # 首次生成：按 bump 类型起步（auto 视同 patch → 0.0.1；
        # major → 1.0.0、minor → 0.1.0，手动指定时不再固定 0.0.1）
        new_ver = bump_version("0.0.0", "patch" if bump == "auto" else bump)

    return AsrPromptVersion(
        version=new_ver,
        generated_at=now,
        wiki_page_count=len(pages),
        term_count=0,  # 由调用方填充
        fingerprint=fingerprint,
    )
