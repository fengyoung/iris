"""ASR 产物的存档目录与保留策略。

``build-asr-prompt`` 每次运行产出三件套（热词 / 替换词典 / 校正提示词），
``--deploy`` 还会备份 VocoType 的三份配置。这些都属于「存档与回滚」，
统一收在 ``output/asr-corrector-bak/`` 下：

    output/asr-corrector-bak/
    ├── asr-hotwords-<date>-<time>.txt
    ├── asr-replace-dict-<date>-<time>.json
    ├── asr-prompt-v<ver>-<date>-<time>.md
    ├── vocotype-backup/<date>-<time>/{hotwords.txt,postprocess.json,ai_settings.json}
    └── legacy/            # 超出保留份数的旧产物

**生效点不在这里**：热词由 ``--deploy`` 写入 VocoType，替换词典与提示词复制到
``data/``。本目录只承载存档与回滚，整目录删掉不影响运行。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from iris.core.locks import FileLock
from iris.utils.paths import get_project_root

logger = logging.getLogger(__name__)

BAK_DIRNAME = "asr-corrector-bak"
LEGACY_DIRNAME = "legacy"
VOCOTYPE_BACKUP_DIRNAME = "vocotype-backup"

#: 每个家族保留的份数，更早的移入 legacy/
DEFAULT_KEEP = 20

#: VocoType 热词表硬上限（超出会被其拒绝，故部署前显式拦截）
HOTWORD_LIMIT = 500

#: 产物家族：(glob 模式, 是否目录, legacy 下的子目录名)
_FAMILIES: Tuple[Tuple[str, bool, str], ...] = (
    ("asr-hotwords-*.txt", False, ""),
    ("asr-replace-dict-*.json", False, ""),
    ("asr-prompt-v*.md", False, ""),
    (f"{VOCOTYPE_BACKUP_DIRNAME}/*", True, VOCOTYPE_BACKUP_DIRNAME),
)


def asr_bak_dir(root: Optional[Path] = None) -> Path:
    """存档根目录（``<项目根>/output/asr-corrector-bak``）。

    调用方应传 ``bundle.root``：``--project-root`` 只传给 ``load_config_bundle``、
    不会写回环境变量，而 ``resolve_data_path`` 的基准是 ``get_project_root()``
    （环境变量或源码位置探测）。不传 root 时二者默认相等，显式 ``--project-root``
    时分叉——整条 ASR 链路（产物 / 存档 / 版本状态 / data 副本）都跟随 root，
    才不会裂成两个根。
    """
    base = Path(root) if root is not None else get_project_root()
    return base / "output" / BAK_DIRNAME


def _sorted_by_recency(entries: List[Path]) -> List[Path]:
    """按修改时间倒序。

    不用文件名排序：``asr-prompt-v4.11.4-20260719`` 与
    ``asr-prompt-v0.0.6-20261008`` 之间，版本号前缀会让字典序把**更旧**的排在
    前面（'4' > '0'），照字典序裁剪会先把最新产物挪进 legacy。
    """
    return sorted(entries, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


def latest_artifact(bak_dir: Path, pattern: str, *, is_dir: bool = False) -> Optional[Path]:
    """取某家族中最新的一项；无产物返回 None。"""
    if not bak_dir.is_dir():
        return None
    entries = [p for p in bak_dir.glob(pattern) if p.is_dir() == is_dir]
    if not entries:
        return None
    return _sorted_by_recency(entries)[0]


def prune_bak(bak_dir: Path, *, keep: int = DEFAULT_KEEP) -> List[Path]:
    """每个家族保留最近 ``keep`` 份，更早的移入 ``legacy/``；返回被移动的条目。

    ``legacy/`` 本身不匹配任何家族模式，故不会被再次裁剪。整个裁剪是读-改-写，
    放进 ``FileLock`` 临界区——两个 build-asr-prompt 并发时会各扫一遍目录再各自
    搬运，后者的 glob 结果可能已被前者搬走。
    """
    if keep <= 0 or not bak_dir.is_dir():
        return []
    legacy = bak_dir / LEGACY_DIRNAME
    moved: List[Path] = []
    with FileLock(bak_dir / ".prune"):
        for pattern, is_dir, legacy_sub in _FAMILIES:
            entries = [p for p in bak_dir.glob(pattern) if p.is_dir() == is_dir]
            for old in _sorted_by_recency(entries)[keep:]:
                dest_dir = legacy / legacy_sub if legacy_sub else legacy
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / old.name
                if dest.exists():
                    # 不覆盖也不静默丢弃：留在原地并告警。静默 continue 会让这个
                    # 条目每轮都被重新尝试、又每轮都被跳过，永远收不进 legacy。
                    logger.warning("存档裁剪跳过（legacy 已有同名）: %s", dest)
                    continue
                shutil.move(str(old), str(dest))
                moved.append(dest)
    return moved
