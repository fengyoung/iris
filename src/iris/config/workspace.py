"""多工作空间管理 — 支持多项目/多团队独立知识库实例。

配置格式（config/workspaces.json）:
    {
      "default_workspace": "main",
      "workspaces": {
        "main": {"source_root": ".../SOURCE/", "wiki_root": ".../LLM-WIKI/", "data_dir": "./data/main"},
        "project_a": {"source_root": ".../project_a/SOURCE/", "wiki_root": ".../project_a/WIKI/", "data_dir": "./data/project_a"}
      }
    }

用法:
    iris --workspace project_a build-wiki --title "xxx"
    iris workspace list
    iris workspace current
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceDef:
    """工作空间定义。"""
    name: str
    source_root: str = ""
    wiki_root: str = ""
    data_dir: str = ""
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source_root": self.source_root,
            "wiki_root": self.wiki_root,
            "data_dir": self.data_dir,
            "description": self.description,
        }


@dataclass
class WorkspaceConfig:
    """多工作空间配置。"""
    default_workspace: str = "main"
    workspaces: Dict[str, WorkspaceDef] = field(default_factory=dict)

    @classmethod
    def load(cls, project_root: Path) -> "WorkspaceConfig":
        """从 config/workspaces.json 加载，不存在则返回默认配置。"""
        config_path = project_root / "config" / "workspaces.json"
        if not config_path.exists():
            logger.debug("工作空间配置文件不存在，使用默认 main 工作空间")
            return cls(default_workspace="main", workspaces={})

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("工作空间配置加载失败: %s，使用默认配置", exc)
            return cls(default_workspace="main", workspaces={})

        workspaces: Dict[str, WorkspaceDef] = {}
        for name, ws_data in data.get("workspaces", {}).items():
            workspaces[name] = WorkspaceDef(
                name=name,
                source_root=ws_data.get("source_root", ""),
                wiki_root=ws_data.get("wiki_root", ""),
                data_dir=ws_data.get("data_dir", ""),
                description=ws_data.get("description", ""),
            )

        return cls(
            default_workspace=data.get("default_workspace", "main"),
            workspaces=workspaces,
        )

    def resolve(self, workspace_name: Optional[str] = None) -> WorkspaceDef:
        """解析工作空间：指定名称 → 默认 → 内置 main。"""
        name = workspace_name or self.default_workspace or "main"
        if name in self.workspaces:
            return self.workspaces[name]
        # 名为 "main" 的内置默认
        if name == "main":
            return WorkspaceDef(name="main")
        raise ValueError(f"工作空间 '{name}' 未在 config/workspaces.json 中定义")

    def list_names(self) -> List[str]:
        return sorted(self.workspaces.keys()) if self.workspaces else ["main"]


class WorkspaceManager:
    """工作空间管理器 — 解析活动工作空间并注入 ConfigBundle。

    用于 CLI 启动时覆盖配置中的路径：
      - config.data_source 中的 source path
      - config.wiki 中的 wiki_root
      - data 目录前缀

    用法:
        mgr = WorkspaceManager(project_root)
        bundle = mgr.apply(bundle, workspace_name="project_a")
    """

    def __init__(self, project_root: Path):
        self._root = project_root
        self._ws_config = WorkspaceConfig.load(project_root)

    @property
    def config(self) -> WorkspaceConfig:
        return self._ws_config

    def resolve(self, workspace_name: Optional[str] = None) -> WorkspaceDef:
        return self._ws_config.resolve(workspace_name)

    def apply(self, bundle: Any, workspace_name: Optional[str] = None) -> Any:
        """将工作空间配置应用到 ConfigBundle。

        覆盖 data_source 中的 source path、wiki 中的 wiki_root、
        以及 data 目录前缀（通过设置环境变量或修改 bundle）。

        Args:
            bundle: ConfigBundle（含 Pydantic ConfigBundleV2）
            workspace_name: 工作空间名称，None 则使用默认

        Returns:
            修改后的 ConfigBundle（原地修改，非深拷贝）
        """
        ws = self.resolve(workspace_name)
        if ws.name == "main" and not ws.source_root and not ws.wiki_root:
            return bundle  # 无自定义配置，直接返回

        bundle if isinstance(bundle, dict) else None

        # 覆盖 data_source path
        if ws.source_root:
            try:
                if hasattr(bundle, "data_source") and hasattr(bundle.data_source, "sources"):
                    for src_name in bundle.data_source.sources:
                        bundle.data_source.sources[src_name].path = ws.source_root
                elif isinstance(getattr(bundle, "data_source", None), dict):
                    ds = bundle.data_source
                    default_src = ds.get("default_source", "")
                    if default_src and default_src in ds.get("sources", {}):
                        ds["sources"][default_src]["path"] = ws.source_root
            except Exception as exc:
                logger.warning("工作空间 source_root 覆盖失败: %s", exc)

        # 覆盖 wiki_root
        if ws.wiki_root:
            try:
                if hasattr(bundle, "wiki") and bundle.wiki is not None:
                    if hasattr(bundle.wiki, "wiki_root"):
                        bundle.wiki.wiki_root = ws.wiki_root
                    elif isinstance(bundle.wiki, dict):
                        bundle.wiki["wiki_root"] = ws.wiki_root
            except Exception as exc:
                logger.warning("工作空间 wiki_root 覆盖失败: %s", exc)

        return bundle

    def list_workspaces(self) -> List[WorkspaceDef]:
        """列出所有已配置的工作空间。"""
        names = self._ws_config.list_names()
        return [self._ws_config.resolve(n) for n in names]
