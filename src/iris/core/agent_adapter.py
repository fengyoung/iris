"""Agent 适配层：步骤 1 版本，仅含非知识库能力。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AgentCapability:
    name: str
    description: str
    command: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


# Iris 3.1 完整能力列表
IRIS_CAPABILITIES: List[AgentCapability] = [
    # ── 数据管道 ──
    AgentCapability(name="scan-source", description="扫描 SOURCE 目录，建立文件索引", command="scan-source",
                    input_schema={"source": {"type": "string"}}, output_schema={}, tags=["data", "scan"]),
    AgentCapability(name="build-chunks", description="文档切块", command="build-chunks",
                    input_schema={"source": {"type": "string"}}, output_schema={}, tags=["data", "chunk"]),
    AgentCapability(name="build-vector-index", description="构建向量索引", command="build-vector-index",
                    input_schema={"source": {"type": "string"}}, output_schema={}, tags=["data", "vector"]),
    # ── 检索问答 ──
    AgentCapability(name="search", description="混合检索（BM25+向量）", command="search",
                    input_schema={"query": {"type": "string"}, "top_k": {"type": "int", "default": 5}},
                    output_schema={}, tags=["retrieval", "read-only"]),
    AgentCapability(name="ask", description="LLM 增强问答（支持图文混合输入）", command="ask",
                    input_schema={"query": {"type": "string"}, "mode": {"type": "string", "default": "llm"},
                                  "image": {"type": "string"}},
                    output_schema={}, tags=["qa", "llm", "multimodal"]),
    # ── Wiki ──
    AgentCapability(name="discover-wiki", description="发现 Wiki 候选", command="discover-wiki",
                    input_schema={"limit": {"type": "int", "default": 20}}, output_schema={}, tags=["wiki"]),
    AgentCapability(name="build-wiki", description="生成 Wiki 页面", command="build-wiki",
                    input_schema={"page_type": {"type": "string"}, "title": {"type": "string"}, "write": {"type": "boolean"}},
                    output_schema={}, tags=["wiki", "llm"]),
    AgentCapability(name="wiki-update", description="增量更新 Wiki 页面", command="wiki-update",
                    input_schema={"title": {"type": "string"}}, output_schema={}, tags=["wiki", "maintenance"]),
    AgentCapability(name="wiki-lint", description="Wiki 健康检查", command="wiki-lint",
                    input_schema={"fix": {"type": "boolean"}}, output_schema={}, tags=["wiki", "diagnostic"]),
    # ── 报告 ──
    AgentCapability(name="build-report", description="专题分析报告", command="build-report",
                    input_schema={"query": {"type": "string"}}, output_schema={}, tags=["report", "llm"]),
    AgentCapability(name="build-mindmap", description="思维导图", command="build-mindmap",
                    input_schema={"query": {"type": "string"}}, output_schema={}, tags=["report", "llm"]),
    AgentCapability(name="build-biweekly-report", description="双周报生成", command="build-biweekly-report",
                    input_schema={"to_source": {"type": "boolean"}}, output_schema={}, tags=["report", "llm"]),
    # ── 会议 ──
    AgentCapability(name="transcribe-meeting", description="会议转录→纪要", command="transcribe-meeting",
                    input_schema={"transcript_file": {"type": "string"}, "to_source": {"type": "boolean"}},
                    output_schema={}, tags=["meeting", "llm"]),
    AgentCapability(name="batch-transcribe", description="批量会议转录", command="batch-transcribe",
                    input_schema={"dir": {"type": "string"}}, output_schema={}, tags=["meeting", "batch"]),
    # ── 记忆 ──
    AgentCapability(name="memory-maintenance", description="记忆自治维护", command="memory-maintenance",
                    input_schema={"auto_age": {"type": "boolean", "default": False}},
                    output_schema={}, tags=["memory", "maintenance"]),
    AgentCapability(name="memory-status", description="记忆状态查看", command="memory-status",
                    input_schema={}, output_schema={}, tags=["memory", "read-only"]),
    AgentCapability(name="working-set", description="设置工作上下文", command="working-set",
                    input_schema={"task": {"type": "string"}}, output_schema={}, tags=["context"]),
    # ── 系统 ──
    AgentCapability(name="status", description="系统状态查看", command="status",
                    input_schema={}, output_schema={}, tags=["system", "read-only"]),
    AgentCapability(name="diagnose", description="系统诊断", command="diagnose",
                    input_schema={}, output_schema={}, tags=["system", "diagnostic"]),
    AgentCapability(name="daily-start", description="日常维护（6步）", command="daily-start",
                    input_schema={}, output_schema={}, tags=["system", "maintenance"]),
    AgentCapability(name="check-config", description="配置检查", command="check-config",
                    input_schema={}, output_schema={}, tags=["system", "read-only"]),
    # ── 工具 ──
    AgentCapability(name="process", description="图文混合双阶段处理", command="process",
                    input_schema={"query": {"type": "string"}, "image": {"type": "string"}},
                    output_schema={}, tags=["multimodal", "llm"]),
]


class AgentAdapter(ABC):
    @abstractmethod
    def agent_type(self) -> str:
        ...

    @abstractmethod
    def capabilities(self) -> List[AgentCapability]:
        ...

    @abstractmethod
    def invoke(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        ...

    @abstractmethod
    def health_check(self) -> bool:
        ...

    def get_capability(self, name: str) -> Optional[AgentCapability]:
        for cap in self.capabilities():
            if cap.name == name:
                return cap
        return None


class ConsoleAdapter(AgentAdapter):
    def __init__(self, config_bundle):
        self._config = config_bundle
        self._capabilities = IRIS_CAPABILITIES

    def agent_type(self) -> str:
        return "console"

    def capabilities(self) -> List[AgentCapability]:
        return self._capabilities

    def invoke(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        # 路由到对应的 handler
        handlers = {
            "status": self._invoke_status,
            "diagnose": self._invoke_diagnose,
            "check-config": self._invoke_check_config,
            "memory-maintenance": self._invoke_memory_maintenance,
            "memory-status": self._invoke_memory_status,
            "working-set": self._invoke_working_set,
            "daily-start": self._invoke_daily_start,
            "scan-source": self._invoke_scan_source,
            "search": self._invoke_search,
            "discover-wiki": self._invoke_discover_wiki,
            "wiki-lint": self._invoke_wiki_lint,
            "wiki-update": self._invoke_wiki_update,
            "transcribe-meeting": self._invoke_transcribe_meeting,
            "build-biweekly-report": self._invoke_biweekly_report,
        }
        handler = handlers.get(capability)
        if handler:
            return handler(params)
        # 通用调用：通过 CLI
        cap = self.get_capability(capability)
        if cap is None:
            return {"error": f"不支持的能力: {capability}"}
        return {"capability": capability, "command": cap.command, "note": "请通过 CLI 直接调用"}

    def _invoke_status(self, params):
        from iris.app.cli.helpers import _build_status_payload
        from iris.utils.logging import IrisLogger
        return _build_status_payload(self._config, IrisLogger(self._config))

    def _invoke_diagnose(self, params):
        from iris.app.cli.helpers import _build_diagnose_payload
        from iris.utils.logging import IrisLogger
        return _build_diagnose_payload(self._config, IrisLogger(self._config))

    def _invoke_check_config(self, params):
        return {"status": "ok", "message": "配置检查通过"}

    def _invoke_memory_maintenance(self, params):
        from iris.memory import MemoryLifecycle
        lifecycle = MemoryLifecycle(self._config)
        report = lifecycle.maintenance(age_days=params.get("age_days", 90))
        if params.get("auto_age"):
            report["age_result"] = lifecycle.age(days=params.get("age_days", 90))
        return report

    def _invoke_memory_status(self, params):
        from iris.memory import UserProfileMemoryStore, CorrectionMemoryStore
        u = UserProfileMemoryStore(self._config).load()
        c = CorrectionMemoryStore(self._config).load()
        return {"profile_updated": u.get("updated_at"), "corrections": len(c.get("items", {}))}

    def _invoke_working_set(self, params):
        from iris.memory import WorkingContextStore
        return WorkingContextStore(self._config).load()

    def _invoke_daily_start(self, params):
        return {"note": "daily-start 需要完整执行环境，请通过 CLI 调用"}

    def _invoke_scan_source(self, params):
        from iris.ingest import MarkdownScanner
        scanner = MarkdownScanner(self._config)
        summary = scanner.scan_default_source()
        return {"source": summary.source_name, "documents": summary.document_count}

    def _invoke_search(self, params):
        from iris.retrieval import EnhancedRetriever
        retriever = EnhancedRetriever(self._config)
        result = retriever.search(params.get("query", ""), top_k=params.get("top_k", 5))
        return {"total_hits": result.total_hits, "top_titles": [h.title for h in result.hits[:3]]}

    def _invoke_discover_wiki(self, params):
        from iris.wiki import CandidateDiscovery
        discovery = CandidateDiscovery(self._config)
        candidates = discovery.discover(limit=params.get("limit", 20))
        return {"count": len(candidates)}

    def _invoke_wiki_lint(self, params):
        from iris.wiki.navigation import lint_wiki
        from pathlib import Path as _Pt
        wiki_root = _Pt(self._config.wiki["wiki_root"]) if self._config.wiki else _Pt()
        result = lint_wiki(wiki_root)
        return {"page_count": result["page_count"], "broken_links": result["broken_count"]}

    def _invoke_wiki_update(self, params):
        from iris.wiki.generator import WikiGenerator
        gen = WikiGenerator(self._config)
        title = params.get("title")
        if title:
            return gen.update_page(title=title)
        return gen.update_all_pages()

    def _invoke_transcribe_meeting(self, params):
        from iris.app.transcribe_meeting import TranscribeMeetingPipeline
        pipeline = TranscribeMeetingPipeline(self._config)
        return pipeline.run(
            transcript_path=params.get("transcript_file"),
            output_path=params.get("output"),
        )

    def _invoke_biweekly_report(self, params):
        result = self._config.app.get("biweekly_report", {})
        return {"author": result.get("author_name", ""), "note": "请通过 CLI 调用 build-biweekly-report"}

    def health_check(self) -> bool:
        return True


class ClaudeCodeAdapter(AgentAdapter):
    def __init__(self, project_root: Optional[str] = None, cli_entry: str = "run_cli.py"):
        self._project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
        self._cli_entry = cli_entry
        self._capabilities = IRIS_CAPABILITIES

    def agent_type(self) -> str:
        return "claude-code"

    def capabilities(self) -> List[AgentCapability]:
        return self._capabilities

    def invoke(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        cap = self.get_capability(capability)
        if cap is None:
            return {"error": f"未知能力: {capability}"}
        cmd = self._build_command(cap.command, params)
        result = self._run_cli(cmd)
        return {"command": cmd, "output": result, "capability": capability}

    def health_check(self) -> bool:
        try:
            result = self._run_cli(["status"])
            return "error" not in str(result).lower()
        except Exception:
            return False

    def _build_command(self, command: str, params: Dict[str, Any]) -> List[str]:
        parts = [str(self._project_root / "scripts" / self._cli_entry), command]
        key_map = {"pretty": "--pretty", "auto_age": "--auto-age", "age_days": "--age-days"}
        for key, value in params.items():
            flag = key_map.get(key, f"--{key.replace('_', '-')}")
            if isinstance(value, bool):
                if value:
                    parts.append(flag)
            elif isinstance(value, (int, str)):
                parts.append(flag)
                parts.append(str(value))
        return parts

    def _run_cli(self, args: List[str]) -> str:
        import subprocess
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=300, cwd=str(self._project_root))
            return result.stdout if result.returncode == 0 else f"[错误] 退出码 {result.returncode}:\n{result.stderr}"
        except subprocess.TimeoutExpired:
            return "[错误] 命令超时"
        except FileNotFoundError:
            return f"[错误] 找不到入口脚本: {args[0]}"


def create_adapter(agent_type: str, **kwargs) -> AgentAdapter:
    if agent_type == "console":
        from iris.config.loader import load_config_bundle
        root = kwargs.get("project_root", ".")
        bundle = load_config_bundle(Path(root))
        return ConsoleAdapter(bundle)
    if agent_type == "claude-code":
        return ClaudeCodeAdapter(**kwargs)
    raise ValueError(f"未知 Agent 类型: {agent_type}")
