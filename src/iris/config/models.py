"""Pydantic v2 配置模型 — 类型安全的配置结构定义。

从 iris2 迁移，适配 iris3 的 4 种 Wiki 页面类型 (domain/concept/project/person)
和 llm.json v3.3 结构（策略开关 + 4 adv_model）。

v3.11: BaseConfigModel 基类提供 __getitem__ / get() 向后兼容，
      使旧代码的 config.llm["models"] 风格访问在 Pydantic 模型上仍可工作。
      渐进迁移完成后再移除这些方法。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, SecretStr, field_validator


class BaseConfigModel(BaseModel):
    """配置模型的基类 — 向后兼容 dict 风格访问。

    允许旧代码以 config["key"] 和 config.get("key", default)
    访问 Pydantic 模型字段，作为属性访问的过渡桥接。
    """

    def __getitem__(self, key: str) -> Any:
        try:
            val = getattr(self, key)
            return val.get_secret_value() if isinstance(val, SecretStr) else val
        except AttributeError:
            raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            val = getattr(self, key)
            return val.get_secret_value() if isinstance(val, SecretStr) else val
        except AttributeError:
            return default


# ── App 配置 ──────────────────────────────────────────────────────


class QAConfig(BaseConfigModel):
    """问答上下文预算配置。"""
    max_prompt_context_chars: int = Field(default=6000, gt=0, description="LLM prompt 最大上下文字符数")
    max_evidence_blocks: int = Field(default=6, gt=0, description="最大证据块数")
    max_wiki_hits: int = Field(default=3, gt=0, description="最大 Wiki 命中数")
    max_block_summary_chars: int = Field(default=300, gt=0, description="每块最大摘要字符数")
    max_wiki_summary_chars: int = Field(default=200, gt=0, description="每条 Wiki 摘要最大字符数")


class OutputConfig(BaseConfigModel):
    """输出配置。"""
    default_output_mode: Literal["chat"] = "chat"
    pretty_by_default: bool = Field(default=False, description="默认人类可读输出")


class SessionConfig(BaseConfigModel):
    """会话配置。"""
    enable_session_memory: bool = Field(default=True, description="启用会话记忆")
    session_timeout_minutes: int = Field(default=30, gt=0, description="会话超时（分钟）")
    session_summary_dir: str = Field(default="./data/memory/sessions", description="会话摘要存储目录")
    max_recent_questions: int = Field(default=8, ge=1, description="最多记录最近问题数")
    max_recent_topics: int = Field(default=12, ge=1, description="最多记录最近主题数")


class PathsConfig(BaseConfigModel):
    """路径配置。"""
    output_dir: str = Field(default="./output")
    temp_dir: str = Field(default="./temp")
    memory_dir: str = Field(default="./memory")
    data_dir: str = Field(default="./data")
    log_dir: str = Field(default="./logs")


class LoggingConfig(BaseConfigModel):
    """日志配置。"""
    log_to_file: bool = Field(default=False)
    level: str = Field(default="INFO")


class SafetyConfig(BaseConfigModel):
    """安全配置。"""
    allowed_write_paths: List[str] = Field(default_factory=list, description="允许写入的路径白名单")
    enforce_write_guard: bool = Field(default=True, description="启用写入守卫")


class AppConfig(BaseConfigModel):
    """应用层配置。"""
    version: str
    app: Dict[str, Any] = Field(default_factory=dict)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    qa: QAConfig = Field(default_factory=QAConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    biweekly_report: Dict[str, Any] = Field(default_factory=dict)
    # 注意：Pydantic 默认丢弃未声明字段（extra="ignore"），app.json 中的段
    # 必须在此声明才能被 config.app.get(...) 读到。
    retrieval: Dict[str, Any] = Field(default_factory=dict)
    organization: Dict[str, Any] = Field(default_factory=dict)
    reminders: Dict[str, Any] = Field(default_factory=dict)
    assistant: Dict[str, Any] = Field(default_factory=dict)


# ── 数据源配置 ────────────────────────────────────────────────────


class DataSourceItem(BaseConfigModel):
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


class IngestionConfig(BaseConfigModel):
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
    chunk_overlap_chars: int = Field(default=150, ge=0)


class DataSourceConfig(BaseConfigModel):
    """数据源配置。"""
    version: str
    default_source: str
    sources: Dict[str, DataSourceItem]
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)

    @field_validator("sources")
    @classmethod
    def at_least_one_enabled(cls, v: Dict[str, DataSourceItem]) -> Dict[str, DataSourceItem]:
        enabled = sum(1 for s in v.values() if s.enabled)
        if enabled == 0 and v:
            import logging
            logging.getLogger(__name__).warning("数据源全部禁用（%d 个已配置，0 个启用）", len(v))
        return v


# ── LLM 配置 ──────────────────────────────────────────────────────


class ModelItem(BaseConfigModel):
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
    supported_inputs: List[Literal["text", "image"]] = Field(default_factory=lambda: ["text"])
    use_cases: List[str] = Field(default_factory=list)
    notes: str = ""
    api_base_url: str
    api_key: SecretStr

    @field_validator("api_base_url")
    @classmethod
    def non_empty_url(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("api_base_url 不能为空")
        return v


class RoleModels(BaseConfigModel):
    """一个角色下的模型注册表。"""
    enabled: bool = True
    default_model_id: str
    models: Dict[str, ModelItem]


class DefaultStrategy(BaseConfigModel):
    """默认路由策略。"""
    default_model_role: str
    fallback_model_role: str
    prefer_lower_cost: bool = True
    allow_auto_upgrade: bool = True
    allow_auto_downgrade: bool = True


class RoutingRule(BaseConfigModel):
    """单条路由规则。"""
    name: str
    enabled: bool = True
    priority: int
    match: Dict[str, str]
    route_to: str
    fallback_to: Optional[str] = None
    description: str = ""


class RoutingConfig(BaseConfigModel):
    """路由配置。"""
    rules: List[RoutingRule]


class EmbeddingConfig(BaseConfigModel):
    """Embedding 配置。"""
    enabled: bool = False
    model: str = "text-embedding-v3"
    api_base_url: str = ""
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    timeout_seconds: int = Field(default=30, gt=0)
    max_retries: int = Field(default=2, ge=0)


class LLMConfig(BaseConfigModel):
    """LLM 层配置。"""
    version: str
    default_strategy: DefaultStrategy
    models: Dict[str, RoleModels]
    routing: RoutingConfig
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)


# ── Wiki 配置 ─────────────────────────────────────────────────────


class PageTypeConfig(BaseConfigModel):
    """单种 Wiki 页面类型配置。"""
    enabled: bool = True
    subdir: str
    filename_prefix: str
    template_name: str


class IndexConfig(BaseConfigModel):
    """Wiki 索引配置。"""
    auto_update: bool = True
    filename: str = "index.md"


class ChangelogConfig(BaseConfigModel):
    """Wiki 变更日志配置。"""
    auto_update: bool = True
    filename: str = "changelog.md"


class WikiConfig(BaseConfigModel):
    """Wiki 配置。"""
    version: str = "3.0"
    wiki_root: str = ""
    page_types: Dict[str, PageTypeConfig] = Field(default_factory=dict)
    index: IndexConfig = Field(default_factory=IndexConfig)
    changelog: ChangelogConfig = Field(default_factory=ChangelogConfig)


# ── 飞书配置 ──────────────────────────────────────────────────────


class FeishuIngestConfig(BaseConfigModel):
    """飞书摄入配置（可选）。"""
    version: str = "3.2"
    doc_convert: Dict[str, Any] = Field(default_factory=dict)
    chat_digest: Dict[str, Any] = Field(default_factory=dict)


# ── 配置聚合根 ────────────────────────────────────────────────────


class ConfigBundleV2(BaseConfigModel):
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
    wiki: Optional[WikiConfig] = None
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

    # ── 工厂方法 ──────────────────────────────────────────────

    @classmethod
    def from_dicts(
        cls,
        *,
        root: Path,
        app_dict: Dict[str, Any],
        data_source_dict: Dict[str, Any],
        llm_dict: Dict[str, Any],
        wiki_dict: Optional[Dict[str, Any]] = None,
        meeting_routes: Optional[Dict[str, Any]] = None,
        feishu_ingest_dict: Optional[Dict[str, Any]] = None,
    ) -> "ConfigBundleV2":
        """从配置字典构建类型安全的 ConfigBundleV2。

        由 loader.load_config_bundle() 调用，传入已解析 ${VAR} 占位符的字典。
        Pydantic 在此自动校验所有字段和约束。

        对于测试/部分构造场景，缺失的必填字段会用占位默认值填充。
        """
        wd = wiki_dict or {}
        fd = feishu_ingest_dict or {}

        # 为部分构造场景填充缺失的必填字段（测试兼容）
        _app = dict(app_dict)
        _app.setdefault("version", "0.0")
        _app.setdefault("app", {})

        _ds = dict(data_source_dict)
        _ds.setdefault("version", "0.0")
        _ds.setdefault("default_source", "_test_default")
        if "sources" not in _ds or not _ds["sources"]:
            _ds["sources"] = {"_test_default": {"enabled": True, "path": str(root)}}

        _llm = dict(llm_dict)
        _llm.setdefault("version", "0.0")
        _llm.setdefault("default_strategy", {"default_model_role": "base_model", "fallback_model_role": "adv_model"})
        _llm.setdefault("models", {"base_model": {"enabled": True, "default_model_id": "_test", "models": {"_test": {"provider": "openai_compatible", "model": "_test", "api_base_url": "http://localhost", "api_key": "", "display_name": "_test", "multimodal": False, "max_context_tokens": 4096, "temperature": 0.2, "timeout_seconds": 10, "max_retries": 0, "priority": 10, "cost_level": "low", "reasoning_level": "standard", "supported_inputs": ["text"], "use_cases": ["qa"], "notes": ""}}}})
        _llm.setdefault("routing", {"rules": []})

        return cls(
            root=root,
            app=AppConfig(**_app),
            data_source=DataSourceConfig(**_ds),
            llm=LLMConfig(**_llm),
            wiki=WikiConfig(**wd) if wd else None,
            feishu_ingest=FeishuIngestConfig(**fd) if fd else None,
            meeting_routes=meeting_routes,
        )

    @classmethod
    def from_config_bundle(
        cls,
        bundle: Any = None,
        *,
        root: Optional[Path] = None,
        app_dict: Optional[Dict[str, Any]] = None,
        data_source_dict: Optional[Dict[str, Any]] = None,
        llm_dict: Optional[Dict[str, Any]] = None,
        wiki_dict: Optional[Dict[str, Any]] = None,
        meeting_routes: Optional[Dict[str, Any]] = None,
        feishu_ingest_dict: Optional[Dict[str, Any]] = None,
    ) -> "ConfigBundleV2":
        """向后兼容：接受旧 ConfigBundle 对象或字典参数。"""
        # 已经是 ConfigBundleV2，直接返回
        if isinstance(bundle, ConfigBundleV2):
            return bundle
        # 是旧 dataclass ConfigBundle
        if bundle is not None and hasattr(bundle, "root"):
            return cls.from_dicts(
                root=bundle.root,
                app_dict=bundle.app if isinstance(bundle.app, dict) else {},
                data_source_dict=bundle.data_source if isinstance(bundle.data_source, dict) else {},
                llm_dict=bundle.llm if isinstance(bundle.llm, dict) else {},
                wiki_dict=bundle.wiki or {},
                meeting_routes=getattr(bundle, "meeting_routes", None),
                feishu_ingest_dict=bundle.feishu_ingest or {},
            )
        return cls.from_dicts(
            root=root or Path("."),
            app_dict=app_dict or {},
            data_source_dict=data_source_dict or {},
            llm_dict=llm_dict or {},
            wiki_dict=wiki_dict,
            meeting_routes=meeting_routes,
            feishu_ingest_dict=feishu_ingest_dict,
        )
