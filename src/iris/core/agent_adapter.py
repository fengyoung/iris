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


# 步骤 1 能力列表（不含知识库相关）
IRIS_CAPABILITIES: List[AgentCapability] = [
    AgentCapability(name="status", description="查看 Iris 系统状态", command="status",
                    input_schema={}, output_schema={"status": "ok|stale", "details": "..."},
                    tags=["system", "read-only"]),
    AgentCapability(name="memory_maintenance", description="记忆自治维护", command="memory-maintenance",
                    input_schema={"auto_age": {"type": "boolean", "default": False}},
                    output_schema={"conflicts": "...", "stale_corrections": "...", "summary": "..."},
                    tags=["memory", "maintenance"]),
    AgentCapability(name="process", description="复杂输入双阶段处理", command="process",
                    input_schema={"query": {"type": "string"}, "image": {"type": "string"}},
                    output_schema={"stage1_output": "string", "stage2_output": "string"},
                    tags=["multimodal", "llm"]),
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
        if capability == "status":
            from iris.app.cli.helpers import _build_status_payload
            from iris.utils.logging import IrisLogger
            return _build_status_payload(self._config, IrisLogger(self._config))
        if capability == "memory_maintenance":
            from iris.memory import MemoryLifecycle
            lifecycle = MemoryLifecycle(self._config)
            report = lifecycle.maintenance(age_days=params.get("age_days", 90))
            if params.get("auto_age"):
                report["age_result"] = lifecycle.age(days=params.get("age_days", 90))
            return report
        return {"error": f"不支持的能力: {capability}"}

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
