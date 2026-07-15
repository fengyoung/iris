"""Wiki 候选发现规则常量。"""

from __future__ import annotations

import re

from ._constants import PAGE_TYPE_PRIORITY

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
    "nbsp 本周工作 本周总结 下周计划 会议内容 会议议程 会议记录 "
    "the and for with from this that are have been".split()
)

LOW_VALUE_TITLES: list[str] = [
    "邮件信息", "会议信息", "会议议题", "下周工作计划", "问题与风险",
    "周报内容", "本周工作总结", "项目背景", "总体目标", "关键举措",
    "章节", "参考来源", "当前结论", "相关依据", "依据摘要", "重点提炼",
    "项目背景与目标", "讨论议题",
    # 章节标题噪音（带序号的结构标题，不含具体业务内容和人名）
    "互动过程中的主要问题和回复",
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

# 通用术语抑制
GENERIC_TERM_SUPPRESS: list[str] = ["AI", "AB", "VS", "OKR", "TODO"]

# 高价值主题提示词
HIGH_VALUE_TOPIC_HINTS: list[str] = ["技术", "系统", "能力", "平台", "架构", "模型", "治理",
                                       "推荐", "搜索", "视觉检测", "鉴定", "识别"]

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
# 注意：\S{2,4} 匹配 2-4 字人名（汉字或非空格字符）
PERSON_PATTERNS: list[re.Pattern] = [
    # ── 结构化标记匹配（高精度） ──

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
    # 供稿/整理/撰写/编辑：XXX
    re.compile(r"(?:供稿|整理|撰写|编辑)[：:]\s*(\S{2,4})"),

    # ── 正文动作匹配（中等精度，捕捉正文中的人名提及） ──

    # 由/让/委派 XXX 负责/主持/牵头/主讲/跟进
    re.compile(r"[由让委派](\S{2,4})(?:负责|主持|牵头|主讲|跟进|协调)"),
    # XXX 提出/汇报了/分享了/总结了/主持了/负责了（排除常见非人称集合主语）
    re.compile(r"(?<!我们|大家|团队|会议|项目|算法|系统|平台|部门|公司|小组)(\S{2,4})(?:提出|汇报了|分享了|总结了|主持了|指出|强调|介绍)"),
    # 据 XXX 介绍/反馈/透露
    re.compile(r"据(\S{2,4})(?:介绍|反馈|透露|汇报)"),
    # 参与人/与会人 列表
    re.compile(r"(?:参与人|参与人员|与会人|与会人员)[：:。，\s]*\n?\s*((?:\S{2,4}[、，,]\s*){1,10}\S{2,4})"),
]

# 候选证据阈值
CANDIDATE_EVIDENCE_THRESHOLDS: dict[str, int] = {
    "project": 2,
    "domain": 3,
    "concept": 4,
    "person": 1,  # 降低阈值：出现 1 次即可候选
}

# 人名排除名单（非人名但容易被误提取的字符串）
PERSON_EXCLUSIONS: frozenset[str] = frozenset(
    "Iris 发言人 主持人 记录人 参会人 参与者 负责人 联系人 整理人 "
    "汇报人 主讲人 审核人 审批人 与会人 面试官 面试者 候选人 招聘方 "
    "委托方 甲方 乙方 供方 需方 我方 你方 用户 客户 商家 买手 卖家 "
    "买家 小编 记者 编辑 作者 整理 校对 审核 批准 签字 确认 "
    "业务方 需求方 供应方 第三方 测试 演示 演示人 旁听 列席 "
    "重点 难点 要点 亮点 热点 痛点 关注 说明 备注 注 摘要 概述".split()
)
