"""Wiki 页面类型常量 —— 单一数据源，供 discovery/generator/navigation/searcher 统一引用。"""

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
