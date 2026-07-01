"""Wiki 页面类型常量 —— 单一数据源，供 discovery/generator/navigation/searcher 统一引用。

所有模块应通过以下访问器获取类型元数据，而非自行 reshape PAGE_TYPE_CONFIG：
  - get_wiki_dir(ptype)          → 目录名
  - get_wiki_prefix(ptype)       → 文件前缀
  - get_display_name(ptype)      → 显示名称
  - get_dir_map()                → {ptype: dir} 映射（替代旧 PAGE_DIRS）
  - get_prefix_map()             → {ptype: prefix} 映射（替代旧 PAGE_PREFIXES）
  - get_display_name_map()       → {ptype: display_name} 映射（替代旧 TYPE_NAMES）
  - get_prefix_to_type_map()     → {prefix: ptype} 反查（替代旧 PREFIX_TO_TYPE）
  - get_type_config_map()        → {ptype: {dir, name}} 映射（替代旧 PAGE_TYPE_CONFIG reshape）
"""

from typing import Dict, List, Tuple

# (目录名, 文件前缀, 显示名称)
PAGE_TYPE_CONFIG: Dict[str, Tuple[str, str, str]] = {
    "domain":   ("01-领域", "领域-", "领域"),
    "concept":  ("02-概念", "概念-", "概念"),
    "project":  ("03-项目", "项目-", "项目"),
    "person":   ("04-人物", "人物-", "人物"),
}

# 便于按显示名称反查
DISPLAY_TO_TYPE: Dict[str, str] = {v[2]: k for k, v in PAGE_TYPE_CONFIG.items()}

# 页面类型优先级（越大越优先，解决跨类型冲突）
PAGE_TYPE_PRIORITY: Dict[str, int] = {
    "project": 3,
    "domain": 2,
    "concept": 2,
    "person": 1,
}

# 陈腐阈值（天数）：超过此天数未更新的 Wiki 页面标记为 stale
STALE_DAYS_THRESHOLD = 30
# lint 检查中使用的陈旧阈值（与 discovery 的 is_wiki_stale 保持一致）
LINT_STALE_DAYS = 90


def get_wiki_dir(page_type: str) -> str:
    """获取页面类型对应的目录名。"""
    return PAGE_TYPE_CONFIG[page_type][0]


def get_wiki_prefix(page_type: str) -> str:
    """获取页面类型对应的文件前缀。"""
    return PAGE_TYPE_CONFIG[page_type][1]


def get_display_name(page_type: str) -> str:
    """获取页面类型的显示名称。"""
    return PAGE_TYPE_CONFIG[page_type][2]


def get_all_types() -> List[str]:
    """获取所有页面类型列表。"""
    return list(PAGE_TYPE_CONFIG.keys())


def get_all_dirs() -> List[str]:
    """获取所有 Wiki 子目录列表。"""
    return [v[0] for v in PAGE_TYPE_CONFIG.values()]


# ── 规范映射访问器（替代各模块自行 reshape） ────────────────────


def get_dir_map() -> Dict[str, str]:
    """{page_type: 目录名} 映射（替代分散的 PAGE_DIRS / PAGE_TYPE_DIRS）。"""
    return {k: v[0] for k, v in PAGE_TYPE_CONFIG.items()}


def get_prefix_map() -> Dict[str, str]:
    """{page_type: 文件前缀} 映射（替代分散的 PAGE_PREFIXES）。"""
    return {k: v[1] for k, v in PAGE_TYPE_CONFIG.items()}


def get_display_name_map() -> Dict[str, str]:
    """{page_type: 显示名称} 映射（替代分散的 TYPE_NAMES）。"""
    return {k: v[2] for k, v in PAGE_TYPE_CONFIG.items()}


def get_prefix_to_type_map() -> Dict[str, str]:
    """{前缀: page_type} 反查映射（替代分散的 PREFIX_TO_TYPE）。"""
    return {v[1]: k for k, v in PAGE_TYPE_CONFIG.items()}


def get_type_config_map() -> Dict[str, Dict[str, str]]:
    """{page_type: {dir, name}} 映射（替代 navigation.py 的局部 reshape）。"""
    return {k: {"dir": v[0], "name": v[2]} for k, v in PAGE_TYPE_CONFIG.items()}
