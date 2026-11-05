"""命令目录：从 CLI argparse 定义自动生成控制台可用的命令元数据与参数表单。

设计约束：
- **单一真相源**：参数类型 / 默认值 / 可选值 / 帮助文本全部来自 ``build_parser()``，
  本模块只维护「命令 → 参数子集」与分组/描述/安全标记，CLI 改参数后表单自动跟随。
- **不改变 CLI 行为**：控制台只把表单值翻译成等价 argv，再交给子进程执行
  ``scripts/run_cli.py``，与 Agent / 终端调用完全一致。
- **安全**：交互式 / 常驻命令一律拒绝从控制台启动；位置参数禁止以 ``-`` 开头，
  选项值统一用 ``--flag=value`` 形式拼接，避免被 argparse 误判为选项。
"""

from __future__ import annotations

import argparse
import math
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from iris.core.exceptions import IrisValueError

__all__ = [
    "CatalogError",
    "ArgSpec",
    "CommandSpec",
    "build_catalog",
    "get_command",
    "build_argv",
    "format_cli",
]


class CatalogError(IrisValueError):
    """控制台参数校验失败（未知命令 / 未知参数 / 类型错误 / 命令被禁止）。"""


# ── 分组 ────────────────────────────────────────────────

GROUPS: List[Tuple[str, str]] = [
    ("daily", "日常"),
    ("qa", "检索问答"),
    ("report", "报告"),
    ("wiki", "Wiki"),
    ("graph", "知识图谱"),
    ("data", "数据管道"),
    ("meeting", "会议与 ASR"),
    ("feishu", "飞书与 Feed"),
    ("memory", "记忆"),
    ("intel", "情报"),
    ("system", "系统"),
    ("other", "其他"),
]


@dataclass(frozen=True)
class _Meta:
    group: str
    desc: str
    args: Tuple[str, ...] = ()
    blocked: str = ""  # 非空 = 禁止从控制台启动的原因
    heavy: bool = False  # 分钟级长任务
    confirm: bool = False  # 有破坏性 / 不可逆写入，前端需二次确认
    readonly: bool = False  # 纯读取，可放心运行
    exclusive: str = ""  # 互斥组名：同组任务同时只允许一个运行
    raw_args: bool = False  # 委托脚本：自由参数文本


_KB = "kb-write"  # 知识库写入互斥组

# fmt: off
_META: Dict[str, _Meta] = {
    # 日常
    "daily-start": _Meta("daily", "每日启动维护：记忆同步、扫描切块、向量索引、Wiki/图谱刷新、用量与提醒",
                         ("dry_run", "no_notify", "force_notify"), heavy=True, exclusive=_KB),
    "signals": _Meta("daily", "主动信号：KR 风险、决策逾期等今日信号摘要", ("dry_run", "no_notify", "force_notify")),
    "reminders": _Meta("daily", "主动提醒：栏目断供 / 成员周报缺失 / 项目停滞（零 LLM）", readonly=True),
    "status": _Meta("daily", "系统状态与数据新鲜度", readonly=True),
    "diagnose": _Meta("daily", "环境诊断", readonly=True),
    "check-config": _Meta("daily", "校验配置文件", readonly=True),
    # 检索问答
    "search": _Meta("qa", "混合检索（BM25 + 向量）", ("query", "mode", "top_k"), readonly=True),
    "ask": _Meta("qa", "知识库问答（支持图片）", ("query", "mode", "top_k", "image")),
    "process": _Meta("qa", "富媒体三阶段处理（图片/PDF/DOCX/视频）", ("query", "image", "output_file")),
    "route-model": _Meta("qa", "查看模型路由结果", ("context",), readonly=True),
    # 报告
    "build-report": _Meta("report", "专题分析报告",
                          ("query", "mode", "top_k", "two_stage", "output_format", "output_file")),
    "build-mindmap": _Meta("report", "思维导图", ("query", "mode", "top_k", "format", "output_file")),
    "build-biweekly-report": _Meta("report", "双周报",
                                   ("query", "as_of", "style_from", "to_source", "dry_run", "output_file"),
                                   heavy=True),
    "okr-check": _Meta("report", "OKR 双周逐项检查", ("days", "kr", "output"), heavy=True),
    "briefing": _Meta("report", "会前情报包", ("topic", "participants", "date", "days", "output")),
    # Wiki
    "discover-wiki": _Meta("wiki", "发现候选 Wiki 主题",
                           ("limit", "incremental", "export_jsonl", "export_review", "export_review_md")),
    "discover-wiki-auto": _Meta("wiki", "自动发现候选主题（增量）"),
    "build-wiki": _Meta("wiki", "生成 Wiki 页面（单页或按审核文件批量）",
                        ("query", "title", "page_type", "review_file", "batch_file", "write", "overwrite", "backup"),
                        heavy=True, exclusive=_KB),
    "build-wiki-nav": _Meta("wiki", "重建 Wiki 导航页"),
    "wiki-pipeline": _Meta("wiki", "Wiki 全流程：发现 → 导出审核文件",
                           ("limit", "incremental", "export_jsonl", "export_review", "export_review_md"),
                           heavy=True, exclusive=_KB),
    "wiki-lint": _Meta("wiki", "Wiki 质量检查（--fix 自动修复）", ("fix",)),
    "wiki-update": _Meta("wiki", "更新单个 Wiki 页面", ("title", "page_type"), heavy=True),
    "enrich-persons": _Meta("wiki", "丰富人物页信息", ("dry_run",), heavy=True, exclusive=_KB),
    "deep-eval": _Meta("wiki", "Wiki 深度评估", ("page_filter", "sample_rate"), heavy=True),
    # 图谱
    "build-graph": _Meta("graph", "构建知识图谱（增量 / 全量 / 单页）", ("full", "page"), heavy=True, exclusive=_KB),
    "graph-query": _Meta("graph", "图谱查询", ("op", "node", "to", "hops", "min_degree"), readonly=True),
    # 数据管道
    "scan-source": _Meta("data", "扫描数据源", ("source", "summary_only", "write_summary")),
    "build-chunks": _Meta("data", "文档切块",
                          ("source", "incremental", "prune_stale", "dry_run", "summary_only", "write_summary"),
                          heavy=True, exclusive=_KB),
    "build-vector-index": _Meta("data", "构建向量索引", ("source", "force_rebuild"), heavy=True, exclusive=_KB),
    "watch": _Meta("data", "监听数据源变更", ("poll_interval", "run_once"), blocked="常驻监听命令，请在终端运行"),
    "frontmatter-batch": _Meta("data", "批量补全 frontmatter",
                               ("source_dirs", "dry_run", "force", "no_llm", "no_wikilink", "no_backup",
                                "list_backups", "restore"), heavy=True, confirm=True, exclusive=_KB),
    # 会议与 ASR
    "transcribe-meeting": _Meta("meeting", "会议录音转写 + 纪要生成 + 路由归档",
                                ("audio_file", "transcript_file", "output", "to_source", "whisper_model", "force"),
                                heavy=True),
    "batch-transcribe": _Meta("meeting", "批量会议转写",
                              ("files", "dir", "output_dir", "to_source", "whisper_model", "force"), heavy=True),
    "build-asr-prompt": _Meta("meeting", "生成 ASR 热词 Prompt",
                              ("profile", "asr_mode", "max_hotwords", "max_mappings", "max_chars", "bump",
                               "deploy", "output_file")),
    "asr-audit": _Meta("meeting", "ASR 热词审计", readonly=True),
    "asr-report": _Meta("meeting", "ASR 纠错反馈报告", ("notes",)),
    "asr-corrector": _Meta("meeting", "ASR 纠错器", ("profile", "correct_mode", "context_ab", "max_asr_length"),
                           blocked="交互式命令（读 stdin），请在终端运行"),
    "meeting-live-assistant": _Meta("meeting", "实时会议参谋", ("asr", "output"),
                                    blocked="实时常驻命令，请在终端运行"),
    # 飞书与 Feed
    "feishu-doc-convert": _Meta("feishu", "飞书文档 → 本地 Markdown 归档",
                                ("url", "from_config", "force", "dry_run"), heavy=True),
    "chat-digest": _Meta("feishu", "聊天记录 AI 提炼",
                         ("group", "user", "range", "from_config", "force", "dry_run"), heavy=True),
    "feed-collect": _Meta("feishu", "Feed 采集：群聊话题挖掘 + 简报",
                          ("chat", "since", "import_mode", "no_extract_docs", "dry_run"), heavy=True),
    "feed-list": _Meta("feishu", "列出关注的会话", readonly=True),
    "feed-pending": _Meta("feishu", "待确认话题", readonly=True),
    "feed-confirm": _Meta("feishu", "确认话题导入", ("topic_id", "all_")),
    "feed-ignore": _Meta("feishu", "忽略话题", ("topic_id",)),
    "feed-add": _Meta("feishu", "添加关注会话", ("chat", "chat_type", "import_mode", "tags")),
    "feed-remove": _Meta("feishu", "移除关注会话", ("chat",), confirm=True),
    "feed-config": _Meta("feishu", "查看 / 修改会话配置", ("chat", "show", "import_mode", "tags")),
    "feed-setup": _Meta("feishu", "Feed 初始化向导", blocked="交互式向导，请在终端运行"),
    # 记忆
    "memory-status": _Meta("memory", "记忆系统概况", readonly=True),
    "memory-list": _Meta("memory", "浏览记忆", ("memory_type",), readonly=True),
    "memory-confirm": _Meta("memory", "确认 / 取消确认纠正规则", ("concept", "unconfirm")),
    "memory-pin": _Meta("memory", "置顶 / 取消置顶纠正规则", ("concept", "unpin")),
    "memory-delete": _Meta("memory", "删除纠正规则", ("concept",), confirm=True),
    "memory-maintenance": _Meta("memory", "记忆维护（老化 / 冲突检测）", ("age_days",)),
    "memory-export": _Meta("memory", "导出记忆", ("output_file",)),
    "memory-import": _Meta("memory", "导入记忆", ("input_file", "replace"), confirm=True),
    "working-show": _Meta("memory", "查看工作上下文", readonly=True),
    "working-set": _Meta("memory", "设置工作上下文",
                         ("task", "pending", "add_pending", "change", "add_change", "notes")),
    "working-clear": _Meta("memory", "清空工作上下文", confirm=True),
    "sync-memory": _Meta("memory", "同步 Claude 工作记忆（委托脚本）", blocked="委托脚本参数尚未校验，请在终端运行"),
    # 情报
    "decisions": _Meta("intel", "决策库：列表 / 搜索 / 审核 / 报告",
                       ("status", "owner", "kr", "since", "days", "query", "reason", "patch", "input_file",
                        "output")),
    "okr-evidence": _Meta("intel", "OKR 证据：查看 / 打标 / 迁移"),
    # 系统
    "usage-stats": _Meta("system", "LLM 用量统计", ("by", "model", "since", "cost"), readonly=True),
    "metrics-export": _Meta("system", "指标导出", ("trend", "weeks"), readonly=True),
    "agent-spec": _Meta("system", "Agent 能力规格", readonly=True),
    "secrets-list": _Meta("system", "列出 Keychain 密钥名", readonly=True),
    "secrets-delete": _Meta("system", "删除 Keychain 密钥", ("key",), confirm=True),
    "secrets-set": _Meta("system", "写入 Keychain 密钥", ("key", "value"),
                         blocked="密钥不经过浏览器，请在终端运行"),
    "llm-bench": _Meta("system", "LLM 通道压测",
                       ("channel", "bench_model", "concurrency", "max_tokens", "phase1_only", "phase2_only"),
                       heavy=True),
    "workspace": _Meta("system", "工作空间：列表 / 当前"),
    "task-panel": _Meta("system", "任务面板守护进程", blocked="控制台自身，请在终端管理"),
    # 其他
    "trello": _Meta("other", "Trello 同步（委托脚本）", blocked="委托脚本参数尚未校验，请在终端运行"),
    "extract-weekly-reports": _Meta("other", "提取成员周报（委托脚本）", blocked="委托脚本参数尚未校验，请在终端运行", heavy=True),
    "extract-travel-invoice": _Meta("other", "提取差旅发票（委托脚本）", blocked="委托脚本参数尚未校验，请在终端运行"),
    "undercover-game": _Meta("other", "谁是卧底（多模型对局）",
                             ("image_a", "image_b", "game_models", "max_players", "max_rounds", "seed",
                              "order_mode", "output_file"), heavy=True),
    "undercover-game-web": _Meta("other", "谁是卧底 Web 版", ("host", "port"), blocked="常驻 Web 服务，请在终端运行"),
}
# fmt: on

# 子操作（位置参数 workspace_action）：命令 → [(值, 标签, 适用参数, 是否需要 subject)]
_ACTIONS: Dict[str, List[Tuple[str, str, Tuple[str, ...], bool]]] = {
    "decisions": [
        ("list", "列表", ("status", "owner", "kr", "since", "days"), False),
        ("search", "搜索", ("status", "owner", "kr", "since", "days"), True),
        (
            "report",
            "报告",
            ("status", "owner", "kr", "since", "days", "query", "output"),
            False,
        ),
        ("show", "详情", (), True),
        ("update", "更新状态", ("status",), True),
        ("pending", "待审核", (), False),
        ("approve", "批准", ("reason", "patch"), True),
        ("reject", "驳回", ("reason",), True),
        ("add", "导入", ("input_file",), False),
        ("export-wiki", "导出 Wiki", (), False),
        ("migrate", "迁移", (), False),
    ],
    "okr-evidence": [
        ("current", "查看证据", (), False),
        ("tag", "打标（LLM）", (), False),
        ("migrate", "迁移", (), False),
    ],
    "workspace": [
        ("current", "当前", (), False),
        ("list", "列表", (), False),
    ],
}

_GLOBAL_ARGS: Tuple[str, ...] = ("workspace",)
_EXCLUDED_DESTS = {
    "help",
    "command",
    "project_root",
    "call_source",
    "pretty",
    "interactive",
    "list_workspaces",
    "workspace_action",
    "subject",
}


# ── 数据结构 ────────────────────────────────────────────


@dataclass
class ArgSpec:
    dest: str
    flag: str
    widget: str  # text / number / checkbox / select
    default: Any = None
    choices: Optional[List[Any]] = None
    help: str = ""
    number_type: Optional[str] = None
    step: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dest": self.dest,
            "flag": self.flag,
            "widget": self.widget,
            "default": self.default,
            "choices": self.choices,
            "help": self.help,
            "number_type": self.number_type,
            "step": self.step,
        }


@dataclass
class CommandSpec:
    name: str
    group: str
    group_label: str
    desc: str
    args: List[ArgSpec] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    blocked: str = ""
    heavy: bool = False
    confirm: bool = False
    readonly: bool = False
    exclusive: str = ""
    raw_args: bool = False

    def arg(self, dest: str) -> Optional[ArgSpec]:
        for a in self.args:
            if a.dest == dest:
                return a
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "group_label": self.group_label,
            "desc": self.desc,
            "args": [a.to_dict() for a in self.args],
            "actions": self.actions,
            "blocked": self.blocked,
            "heavy": self.heavy,
            "confirm": self.confirm,
            "readonly": self.readonly,
            "exclusive": self.exclusive,
            "raw_args": self.raw_args,
        }


# ── 构建 ────────────────────────────────────────────────


def _arg_spec(action: argparse.Action) -> ArgSpec:
    flag = next(
        (s for s in action.option_strings if s.startswith("--")),
        action.option_strings[0],
    )
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        widget = "checkbox"
    elif action.choices:
        widget = "select"
    elif action.type in (int, float):
        widget = "number"
    else:
        widget = "text"
    choices = list(action.choices) if action.choices else None
    default = action.default if action.default is not argparse.SUPPRESS else None
    return ArgSpec(
        dest=action.dest,
        flag=flag,
        widget=widget,
        default=default,
        choices=choices,
        help=action.help or "",
        number_type="int"
        if action.type is int
        else "float"
        if action.type is float
        else None,
        step="any" if action.type is float else 1 if action.type is int else None,
    )


def _parser_actions() -> Dict[str, argparse.Action]:
    from iris.app._cli_main import build_parser

    parser = build_parser()
    return {
        a.dest: a
        for a in parser._actions
        if a.option_strings and a.dest not in _EXCLUDED_DESTS
    }


_CACHE: Optional[Dict[str, CommandSpec]] = None


def build_catalog(*, refresh: bool = False) -> Dict[str, CommandSpec]:
    """构建完整命令目录（命令名 → CommandSpec）。结果按进程缓存。"""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    from iris.app._cli_main import COMMANDS

    actions = _parser_actions()
    labels = dict(GROUPS)
    catalog: Dict[str, CommandSpec] = {}
    if set(_META) != set(COMMANDS):
        raise CatalogError("命令目录映射与 CLI 不一致")
    for name in COMMANDS:
        meta = _META[name]
        dests = [*meta.args, *_GLOBAL_ARGS]
        if set(dests) - actions.keys():
            raise CatalogError(f"{name} 参数映射不存在")
        for _value, _label, action_args, _subject in _ACTIONS.get(name, []):
            if not set(action_args) <= set(meta.args):
                raise CatalogError(f"{name} 子操作参数映射不存在")
        spec = CommandSpec(
            name=name,
            group=meta.group,
            group_label=labels.get(meta.group, meta.group),
            desc=meta.desc,
            args=[_arg_spec(actions[d]) for d in dests],
            actions=[
                {"value": v, "label": lbl, "args": list(a), "subject": subj}
                for v, lbl, a, subj in _ACTIONS.get(name, [])
            ],
            blocked=meta.blocked,
            heavy=meta.heavy,
            confirm=meta.confirm,
            readonly=meta.readonly,
            exclusive=meta.exclusive,
            raw_args=meta.raw_args,
        )
        catalog[name] = spec
    _CACHE = catalog
    return catalog


def get_command(name: str) -> CommandSpec:
    spec = build_catalog().get(name)
    if spec is None:
        raise CatalogError(f"未知命令: {name}")
    return spec


def catalog_payload() -> Dict[str, Any]:
    """供 /api/catalog 返回的 JSON。"""
    cat = build_catalog()
    return {
        "groups": [{"id": g, "label": lbl} for g, lbl in GROUPS],
        "commands": [c.to_dict() for c in cat.values()],
    }


# ── argv 生成 ───────────────────────────────────────────


def _coerce_flag(arg: ArgSpec, value: Any) -> Optional[str]:
    """把单个参数值转成 argv 片段；返回 None 表示省略（空值 / 未勾选 / 等于默认值）。"""
    if arg.widget == "checkbox":
        if type(value) is not bool:
            raise CatalogError(f"{arg.flag} 需要布尔值")
        return arg.flag if value else None
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if arg.widget == "number":
        label = "整数" if arg.number_type == "int" else "有限数字"
        if type(value) not in (str, int, float):
            raise CatalogError(f"{arg.flag} 需要{label}: {value!r}")
        num: int | float
        try:
            if arg.number_type == "int":
                if isinstance(value, str) and not re.fullmatch(
                    r"[+-]?[0-9]+", value.strip()
                ):
                    raise ValueError
                if isinstance(value, float) and (
                    not math.isfinite(value) or not value.is_integer()
                ):
                    raise ValueError
                num = int(value)
            else:
                num = float(value)
                if not math.isfinite(num):
                    raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise CatalogError(f"{arg.flag} 需要{label}: {value!r}") from None
        if num == arg.default:
            return None
        return f"{arg.flag}={num}"
    if not isinstance(value, str):
        raise CatalogError(f"{arg.flag} 需要文本")
    text = str(value)
    if "\x00" in text or "\n" in text:
        raise CatalogError(f"{arg.flag} 含非法字符")
    if arg.choices is not None:
        if text not in [str(c) for c in arg.choices]:
            raise CatalogError(f"{arg.flag} 取值无效: {text!r}，可选: {arg.choices}")
        if text == str(arg.default):
            return None
    return f"{arg.flag}={text}"


def _positional(value: Any, label: str) -> str:
    text = str(value).strip()
    if text.startswith("-"):
        raise CatalogError(f"{label} 不能以 '-' 开头")
    if "\x00" in text or "\n" in text:
        raise CatalogError(f"{label} 含非法字符")
    return text


def _resolve_action(spec: CommandSpec, params: Dict[str, Any]) -> Tuple[List[str], set]:
    """解析子操作，返回 (位置参数 argv, 允许的参数集合)。"""
    allowed = {a.dest for a in spec.args}
    action = params.pop("action", "")
    subject = params.pop("subject", "")
    if not isinstance(action, str) or not isinstance(subject, str):
        raise CatalogError("子操作与目标需要文本")
    action = action.strip()
    subject = subject.strip()
    if not spec.actions:
        if action or subject:
            raise CatalogError(f"{spec.name} 不支持子操作")
        return [], allowed
    by_value = {a["value"]: a for a in spec.actions}
    if not action:
        action = spec.actions[0]["value"]
    if action not in by_value:
        raise CatalogError(f"{spec.name} 子操作无效: {action!r}")
    info = by_value[action]
    if info["subject"] and not subject:
        raise CatalogError(f"{spec.name} {action} 需要目标（编号或搜索词）")
    allowed = set(info["args"]) | set(_GLOBAL_ARGS)
    pos = [_positional(action, "子操作")]
    if subject and not info["subject"]:
        raise CatalogError(f"{spec.name} {action} 不接受目标")
    if subject:
        pos.append(_positional(subject, "目标"))
    return pos, allowed


def build_argv(command: str, params: Optional[Dict[str, Any]] = None) -> List[str]:
    """把表单参数翻译为 ``run_cli.py`` 的 argv（不含解释器与脚本路径）。

    params 键为参数 dest；可选 ``action`` / ``subject``（子操作与目标）、
    委托脚本不允许从控制台启动。
    """
    spec = get_command(command)
    if spec.blocked:
        raise CatalogError(f"{command} 不支持从控制台启动：{spec.blocked}")
    params = dict(params or {})
    positional, allowed = _resolve_action(spec, params)
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise CatalogError(f"{command} 不支持参数: {', '.join(unknown)}")
    flags: List[str] = []
    for arg in spec.args:
        if arg.dest in params:
            piece = _coerce_flag(arg, params[arg.dest])
            if piece:
                flags.append(piece)
    return [command, *positional, *flags]


def format_cli(argv: Sequence[str]) -> str:
    """等价 CLI 命令行（展示用，可直接粘贴到终端）。"""
    return "python3 scripts/run_cli.py " + " ".join(shlex.quote(a) for a in argv)
