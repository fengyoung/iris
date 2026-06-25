"""Wiki 候选发现规则常量。"""

from __future__ import annotations

import re

# 标题前缀 → 页面类型映射（用于 infer_page_type）
HEADING_PREFIXES: list[tuple[str, str]] = [
    ("项目", "project"),
    ("方案", "project"),
    ("专项", "project"),
    ("机制", "domain"),
    ("流程", "domain"),
    ("规范", "domain"),
    ("策略", "domain"),
    ("技术", "domain"),
    ("平台", "domain"),
    ("架构", "domain"),
    ("体系", "domain"),
    ("框架", "concept"),
    ("模型", "concept"),
    ("算法", "concept"),
    ("方法", "concept"),
    ("协议", "concept"),
]

# 术语提取模式
TERM_PATTERNS = [
    re.compile(r"\b[A-Z]{2,}(?:[A-Za-z0-9\-]*[a-z])?(?:[A-Za-z0-9\-]*)\b"),
    re.compile(r"[A-Za-z0-9_\-\一-鿿]{2,30}"),
]

STOPWORDS = frozenset(
    "项目 方案 机制 讨论 过程 内容 目标 背景 当前 总结 计划 工作 部门 周报 "
    "the and for with from this that are have been".split()
)

LOW_VALUE_TITLES: list[str] = [
    "邮件信息", "会议信息", "会议议题", "下周工作计划", "问题与风险",
    "周报内容", "本周工作总结", "项目背景", "总体目标", "关键举措",
    "章节", "参考来源", "当前结论", "相关依据", "依据摘要", "重点提炼",
    "项目背景与目标", "讨论议题",
]

LOW_VALUE_PREFIXES: list[str] = ["邮件", "会议", "本周", "下周", "问题", "相关", "当前"]

STRUCTURAL_TITLES: list[str] = [
    "项目背景", "项目目标", "总体目标", "关键举措", "背景", "目标",
    "结论", "总结", "概述", "讨论记录", "行动方案", "讨论全过程",
    "参考来源", "依据摘要", "相关依据", "当前结论",
]

# 英文大写术语最小长度
MIN_TERM_LENGTH = 2

# 项目名后缀模式
PROJECT_SUFFIX_PATTERNS: list[re.Pattern] = [
    re.compile(r"\s*-\s*\d{4}年行动方案$"),
    re.compile(r"行动方案$"),
    re.compile(r"讨论全过程$"),
    re.compile(r"项目讨论$"),
]

LEADING_ENUM_RE = re.compile(r"^\d+[\.\、\s]+")
ONLY_SECTION_RE = re.compile(r"^[\d\.\s\一-鿿h]{1,8}$")

# 页面类型优先级（高→低用于合并冲突）
PAGE_TYPE_PRIORITY: dict[str, int] = {"project": 3, "domain": 2, "concept": 2, "person": 1}

# 通用术语抑制
GENERIC_TERM_SUPPRESS: list[str] = ["AI", "AB", "VS", "OKR", "TODO"]

# 高价值主题提示词
HIGH_VALUE_TOPIC_HINTS: list[str] = ["技术", "系统", "能力", "平台", "架构", "模型", "治理",
                                       "推荐", "搜索", "图验", "验真", "识别"]

# 低价值路径提示
LOW_VALUE_PATH_HINTS: list[str] = ["周报", "模板", "例会", "邮件"]

# 低价值术语模式
LOW_VALUE_TERM_PATTERNS: list[re.Pattern] = [
    re.compile(r"^V?\d+(?:\.\d+)+$"),
    re.compile(r"^[A-Z]{1,2}$"),
    re.compile(r"^AI.*评测"),
]

# 路径权重
PATH_WEIGHTS: dict[str, int] = {
    "讨论思考": 4,
    "方案及汇报": 4,
    "目标管理": 4,
    "会议纪要": 3,
    "部门管理": 3,
    "周报": 1,
}

# 人物发现模式（从文档中提取人名）
PERSON_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:负责人|联系人|作者|汇报人|主讲)[：:]\s*(\S{2,4})"),
    re.compile(r"(?:@)(\S{2,4})(?:\s|$|[，,。])"),
]

# 候选证据阈值
CANDIDATE_EVIDENCE_THRESHOLDS: dict[str, int] = {
    "project": 2,
    "domain": 3,
    "concept": 3,
    "person": 2,
}
