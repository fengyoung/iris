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
    "日期 序号 负责人 参会人员 截止时间 关键数据 辅助整理 基础模型 审核员 "
    "nbsp liubei 本周工作 本周总结 下周计划 会议内容 会议议程 会议记录 "
    "the and for with from this that are have been exampleorg".split()
)

LOW_VALUE_TITLES: list[str] = [
    "邮件信息", "会议信息", "会议议题", "下周工作计划", "问题与风险",
    "周报内容", "本周工作总结", "项目背景", "总体目标", "关键举措",
    "章节", "参考来源", "当前结论", "相关依据", "依据摘要", "重点提炼",
    "项目背景与目标", "讨论议题",
    # 章节标题噪音
    "一、图像采集3.0主观项检测", "二、设备推进与现场实施",
    "一、推荐系统的基本信息", "二、低价与好成色商品分档提权策略",
    "一、外观瑕疵检测与算法优化进展", "一、上周待办事项回顾与进展同步",
    "三、下一步重点计划与关键技术优化", "四、后续里程碑拆解与流程优化方向",
    "五、AI平台功能演进与多模态能力建设", "九、近期实验汇总与上线情况",
    "二、已识别的AI应用场景及能力验证", "四、项目推进机制与组织保障",
    "青岛大仓调试总结（唐超）", "互动过程中的主要问题和回复",
]

LOW_VALUE_PREFIXES: list[str] = [
    "邮件", "会议", "本周", "下周", "问题", "相关", "当前",
    "✅", "一、", "二、", "三、", "四、", "五、", "六、", "七、", "八、", "九、",
    "议题", "议题一", "议题二", "议题三", "议题四", "议题五",
]

STRUCTURAL_TITLES: list[str] = [
    "项目背景", "项目目标", "总体目标", "关键举措", "背景", "目标",
    "结论", "总结", "概述", "讨论记录", "行动方案", "讨论全过程",
    "参考来源", "依据摘要", "相关依据", "当前结论",
]

# 英文大写术语最小长度
MIN_TERM_LENGTH = 3

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
    re.compile(r"^-{2,}$"),                   # --- 等 Markdown 分隔线
    re.compile(r"^\d{1,3}$"),                 # 纯数字 0-999
    re.compile(r"^[A-Za-z]+\.(com|cn|net)$"), # 域名
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
    # 负责人/联系人/作者 等角色标记
    re.compile(r"(?:负责人|联系人|作者|汇报人|主讲|记录人)[：:]\s*(\S{2,4})"),
    # @提及
    re.compile(r"(?:@)(\S{2,4})(?:\s|$|[，,。])"),
    # 责任人标记
    re.compile(r"(?:\*\*)?责任人(?:\*\*)?[：:]\s*(\S{2,4})"),
    # 项目整体负责人
    re.compile(r"项目整体负责人[：:]\s*(\S{2,4})"),
    # 参会人员列表（## 参会人员\n姓名、姓名、…）
    re.compile(r"(?:参会人员|出席人员|参会人)[：:。，\s]*\n?\s*((?:\S{2,4}[、，,]\s*){1,10}\S{2,4})"),
]

# 候选证据阈值
CANDIDATE_EVIDENCE_THRESHOLDS: dict[str, int] = {
    "project": 2,
    "domain": 3,
    "concept": 4,
    "person": 2,
}
