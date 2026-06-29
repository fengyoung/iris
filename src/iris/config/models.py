"""Pydantic v2 配置模型 — 类型安全的配置结构定义。

从 iris2 迁移，适配 iris3 的 4 种 Wiki 页面类型 (domain/concept/project/person)
和 llm.json v3.3 结构（策略开关 + 4 adv_model）。

与 config/loader.py 的 ConfigBundle（Dict 访问）并存，
通过 ConfigBundleV2.from_config_bundle() 渐进迁移。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ── App 配置 ──────────────────────────────────────────────────────


class QAConfig(BaseModel):
    """问答上下文预算配置。"""
    max_prompt_context_chars: int = Field(default=6000, gt=0, description="LLM prompt 最大上下文字符数")
    max_evidence_blocks: int = Field(default=6, gt=0, description="最大证据块数")
    max_wiki_hits: int = Field(default=3, gt=0, description="最大 Wiki 命中数")
    max_block_summary_chars: int = Field(default=300, gt=0, description="每块最大摘要字符数")
    max_wiki_summary_chars: int = Field(default=200, gt=0, description="每条 Wiki 摘要最大字符数")


class OutputConfig(BaseModel):
    """输出配置。"""
    default_output_mode: Literal["chat"] = "chat"
    pretty_by_default: bool = Field(default=False, description="默认人类可读输出")


class SessionConfig(BaseModel):
    """会话配置。"""
    enable_session_memory: bool = Field(default=True, description="启用会话记忆")
    session_timeout_minutes: int = Field(default=30, gt=0, description="会话超时（分钟）")
    max_recent_questions: int = Field(default=8, ge=1, description="最多记录最近问题数")
    max_recent_topics: int = Field(default=12, ge=1, description="最多记录最近主题数")


class PathsConfig(BaseModel):
    """路径配置。"""
    output_dir: str = Field(default="./output")
    temp_dir: str = Field(default="./temp")
    memory_dir: str = Field(default="./memory")
    data_dir: str = Field(default="./data")
    log_dir: str = Field(default="./logs")


class LoggingConfig(BaseModel):
    """日志配置。"""
    log_to_file: bool = Field(default=False)
    level: str = Field(default="INFO")


class SafetyConfig(BaseModel):
    """安全配置。"""
    allowed_write_paths: List[str] = Field(default_factory=list, description="允许写入的路径白名单")
    enforce_write_guard: bool = Field(default=True, description="启用写入守卫")


class AppConfig(BaseModel):
    """应用层配置。"""
    version: str
    app: Dict[str, Any] = Field(default_factory=dict)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    qa: QAConfig = Field(default_factory=QAConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)


# ── 数据源配置 ────────────────────────────────────────────────────


class DataSourceItem(BaseModel):
    """单个数据源配置。"""
    enabled: bool = True
    name: str = ""
    path: str
    format: Literal["markdown"] = "markdown"
    read_only: bool = True
    recursive: bool = True
    include_patterns: List[str] = Field(default_factory=lambda: ["**/*.md"])
    exclude_patterns: List[str] = Field(default_factory=list)
    follow_symlinks: bool = False
    extract_metadata: bool = True
    notes: str = ""


class IngestionConfig(BaseModel):
    """摄入配置。"""
    scan_on_startup: bool = True
    incremental_scan: bool = True
    chunk_strategy: str = "markdown_section"
    max_file_size_mb: int = Field(default=20, gt=0)
    encoding: str = "utf-8"
    store_file_hash: bool = True
    store_mtime: bool = True
    max_chunk_chars: int = Field(default=1200, gt=0)
    max_preview_chars: int = Field(default=180, gt=0)


class DataSourceConfig(BaseModel):
    """数据源配置。"""
    version: str
    default_source: str
    sources: Dict[str, DataSourceItem]
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)

    @field_validator("sources")
    @classmethod
    def at_least_one_enabled(cls, v: Dict[str, DataSourceItem]) -> Dict[str, DataSourceItem]:
        enabled = sum(1 for s in v.values() if s.enabled)
        if enabled == 0:
            raise ValueError("至少需要启用一个数据源")
        return v


# ── LLM 配置 ──────────────────────────────────────────────────────


class ModelItem(BaseModel):
    """单个模型配置。"""
    provider: str
    model: str
    display_name: str = ""
    multimodal: bool = False
    max_context_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.2, ge=0, le=2)
    top_p: float = Field(default=1.0, ge=0, le=1)
    timeout_seconds: int = Field(default=60, gt=0)
    max_retries: int = Field(default=2, ge=0)
    priority: int = Field(default=0)
    cost_level: str = Field(default="low")
    reasoning_level: Literal["standard", "advanced"] = "standard"
    supported_inputs: List[Literal["text", "image"]]
    use_cases: List[str]
    notes: str = ""
    api_base_url: str
    api_key: str

    @field_validator("api_base_url")
    @classmethod
    def non_empty_url(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("api_base_url 不能为空")
        return v


class RoleModels(BaseModel):
    """一个角色下的模型注册表。"""
    enabled: bool = True
    default_model_id: str
    models: Dict[str, ModelItem]

    @field_validator("models")
    @classmethod
    def default_in_models(cls, v: Dict[str, ModelItem], info: Any) -> Dict[str, ModelItem]:
        return v


class DefaultStrategy(BaseModel):
    """默认路由策略。"""
    default_model_role: str
    fallback_model_role: str
    prefer_lower_cost: bool = True
    allow_auto_upgrade: bool = True
    allow_auto_downgrade: bool = True


class RoutingRule(BaseModel):
    """单条路由规则。"""
    name: str
    enabled: bool = True
    priority: int
    match: Dict[str, str]
    route_to: str
    fallback_to: Optional[str] = None
    description: str = ""


class RoutingConfig(BaseModel):
    """路由配置。"""
    rules: List[RoutingRule]


class EmbeddingConfig(BaseModel):
    """Embedding 配置。"""
    enabled: bool = False
    model: str = "text-embedding-v3"
    api_base_url: str = ""
    api_key: str = ""
    timeout_seconds: int = Field(default=30, gt=0)
    max_retries: int = Field(default=2, ge=0)


class LLMConfig(BaseModel):
    """LLM 层配置。"""
    version: str
    default_strategy: DefaultStrategy
    models: Dict[str, RoleModels]
    routing: RoutingConfig
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)


# ── Wiki 配置 ─────────────────────────────────────────────────────


class PageTypeConfig(BaseModel):
    """单种 Wiki 页面类型配置。"""
    enabled: bool = True
    subdir: str
    filename_prefix: str
    template_name: str


class IndexConfig(BaseModel):
    """Wiki 索引配置。"""
    auto_update: bool = True
    filename: str = "index.md"


class ChangelogConfig(BaseModel):
    """Wiki 变更日志配置。"""
    auto_update: bool = True
    filename: str = "changelog.md"


class WikiConfig(BaseModel):
    """Wiki 配置。"""
    version: str
    wiki_root: str
    page_types: Dict[str, PageTypeConfig]
    index: IndexConfig = Field(default_factory=IndexConfig)
    changelog: ChangelogConfig = Field(default_factory=ChangelogConfig)


# ── 飞书配置 ──────────────────────────────────────────────────────


class FeishuIngestConfig(BaseModel):
    """飞书摄入配置（可选）。"""
    version: str = "3.2"
    doc_convert: Dict[str, Any] = Field(default_factory=dict)
    chat_digest: Dict[str, Any] = Field(default_factory=dict)


# ── 配置聚合根 ────────────────────────────────────────────────────


class ConfigBundleV2(BaseModel):
    """类型安全的配置聚合根（Pydantic v2）。

    对应 config/loader.py 的 ConfigBundle（Dict 访问），
    提供完整的类型安全访问和 IDE 自动补全。

    用法:
        # 方式 1：从 ConfigBundle 转换（推荐）
        bundle = load_config_bundle(project_root)
        v2 = ConfigBundleV2.from_config_bundle(bundle)

        # 方式 2：直接构造
        v2 = ConfigBundleV2(
            root=Path("."),
            app=AppConfig(**app_dict),
            data_source=DataSourceConfig(**ds_dict),
            llm=LLMConfig(**llm_dict),
            wiki=WikiConfig(**wiki_dict),
        )

        # 类型安全访问
        qa = v2.app.qa
        max_chars = qa.max_prompt_context_chars  # IDE 自动补全，类型 int
    """

    model_config = {"arbitrary_types_allowed": True}

    root: Path
    app: AppConfig
    data_source: DataSourceConfig
    llm: LLMConfig
    wiki: WikiConfig
    feishu_ingest: Optional[FeishuIngestConfig] = None
    meeting_routes: Optional[Dict[str, Any]] = None

    # ── 便捷属性 ──────────────────────────────────────────────

    @property
    def default_source_name(self) -> str:
        return self.data_source.default_source

    @property
    def default_source_path(self) -> Path:
        return Path(self.data_source.sources[self.default_source_name].path)

    @property
    def wiki_root_path(self) -> Path:
        return Path(self.wiki.wiki_root)

    @property
    def metadata_dir(self) -> Path:
        return self.root / "data" / "metadata"

    # ── 从 ConfigBundle 转换 ──────────────────────────────────

    @classmethod
    def from_config_bundle(cls, bundle: Any) -> "ConfigBundleV2":
        """从现有 ConfigBundle（dataclass, Dict 访问）转换为类型安全版本。

        Args:
            bundle: config/loader.py 的 ConfigBundle 实例

        Returns:
            ConfigBundleV2 实例
        """
        wiki_dict = bundle.wiki or {}
        feishu_dict = bundle.feishu_ingest or {}
        return cls(
            root=bundle.root,
            app=AppConfig(**bundle.app),
            data_source=DataSourceConfig(**bundle.data_source),
            llm=LLMConfig(**bundle.llm),
            wiki=WikiConfig(**wiki_dict) if wiki_dict else WikiConfig(
                version="3.2",
                wiki_root="",
                page_types={
                    "domain": PageTypeConfig(subdir="01-领域", filename_prefix="领域-", template_name="domain.md"),
                    "concept": PageTypeConfig(subdir="02-概念", filename_prefix="概念-", template_name="concept.md"),
                    "project": PageTypeConfig(subdir="03-项目", filename_prefix="项目-", template_name="project.md"),
                    "person": PageTypeConfig(subdir="04-人物", filename_prefix="人物-", template_name="person.md"),
                },
            ),
            feishu_ingest=FeishuIngestConfig(**feishu_dict) if feishu_dict else None,
            meeting_routes=bundle.meeting_routes,
        )
