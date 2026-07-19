"""config/models.py Pydantic 单元测试 — 验证模型结构、默认值、validator。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from iris.config.models import (
    AppConfig,
    BaseConfigModel,
    DataSourceConfig,
    DataSourceItem,
    EmbeddingConfig,
    IngestionConfig,
    LLMConfig,
    ModelItem,
    PathsConfig,
    QAConfig,
    RoleModels,
    RoutingConfig,
    SafetyConfig,
    SessionConfig,
    WikiConfig,
)


# ── BaseConfigModel ────────────────────────────────────────────────

class TestBaseConfigModel:
    """向后兼容 dict 风格访问。"""

    class _Simple(BaseConfigModel):
        name: str = "iris"
        value: int = 42

    def test_getitem_existing_key(self):
        m = self._Simple()
        assert m["name"] == "iris"
        assert m["value"] == 42

    def test_getitem_missing_key_raises_key_error(self):
        m = self._Simple()
        with pytest.raises(KeyError):
            _ = m["nonexistent"]

    def test_get_existing_key(self):
        m = self._Simple()
        assert m.get("name") == "iris"

    def test_get_missing_key_returns_default(self):
        m = self._Simple()
        assert m.get("nonexistent", "fallback") == "fallback"

    def test_get_missing_key_returns_none_when_no_default(self):
        m = self._Simple()
        assert m.get("nonexistent") is None


# ── QAConfig ───────────────────────────────────────────────────────

class TestQAConfig:
    def test_defaults(self):
        qa = QAConfig()
        assert qa.max_prompt_context_chars == 6000
        assert qa.max_evidence_blocks == 6
        assert qa.max_wiki_hits == 3
        assert qa.max_block_summary_chars == 300
        assert qa.max_wiki_summary_chars == 200

    def test_custom_values(self):
        qa = QAConfig(max_prompt_context_chars=10000, max_evidence_blocks=12)
        assert qa.max_prompt_context_chars == 10000
        assert qa.max_evidence_blocks == 12

    def test_zero_value_raises_validation_error(self):
        with pytest.raises(ValidationError):
            QAConfig(max_prompt_context_chars=0)

    def test_negative_value_raises_validation_error(self):
        with pytest.raises(ValidationError):
            QAConfig(max_evidence_blocks=-1)


# ── SessionConfig ──────────────────────────────────────────────────

class TestSessionConfig:
    def test_defaults(self):
        s = SessionConfig()
        assert s.enable_session_memory is True
        assert s.session_timeout_minutes == 30
        assert s.max_recent_questions == 8
        assert s.max_recent_topics == 12

    def test_zero_timeout_raises(self):
        with pytest.raises(ValidationError):
            SessionConfig(session_timeout_minutes=0)

    def test_zero_recent_questions_raises(self):
        with pytest.raises(ValidationError):
            SessionConfig(max_recent_questions=0)


# ── PathsConfig ────────────────────────────────────────────────────

class TestPathsConfig:
    def test_defaults(self):
        p = PathsConfig()
        assert p.output_dir == "./output"
        assert p.data_dir == "./data"

    def test_custom_paths(self):
        p = PathsConfig(output_dir="/custom/output", data_dir="/custom/data")
        assert p.output_dir == "/custom/output"


# ── SafetyConfig ───────────────────────────────────────────────────

class TestSafetyConfig:
    def test_defaults(self):
        s = SafetyConfig()
        assert s.enforce_write_guard is True
        assert s.allowed_write_paths == []

    def test_allowed_paths(self):
        s = SafetyConfig(allowed_write_paths=["/data", "/output"])
        assert len(s.allowed_write_paths) == 2


# ── AppConfig ──────────────────────────────────────────────────────

class TestAppConfig:
    def test_minimal_construction(self):
        cfg = AppConfig(version="3.19.6")
        assert cfg.version == "3.19.6"

    def test_nested_defaults(self):
        cfg = AppConfig(version="3.0")
        assert cfg.qa.max_prompt_context_chars == 6000
        assert cfg.session.session_timeout_minutes == 30
        assert cfg.safety.enforce_write_guard is True

    def test_nested_override(self):
        cfg = AppConfig(version="3.0", qa={"max_evidence_blocks": 20})
        assert cfg.qa.max_evidence_blocks == 20

    def test_getitem_on_nested(self):
        cfg = AppConfig(version="3.0")
        assert cfg["version"] == "3.0"
        # 嵌套模型也支持 __getitem__
        assert cfg.qa["max_evidence_blocks"] == 6


# ── ModelItem ──────────────────────────────────────────────────────

class TestModelItem:
    _BASE = dict(provider="deepseek", model="deepseek-chat",
                 api_base_url="https://api.deepseek.com/v1", api_key="sk-test")

    def test_valid_model(self):
        m = ModelItem(**self._BASE)
        assert m.provider == "deepseek"
        assert m.temperature == 0.2

    def test_empty_api_base_url_raises(self):
        data = {**self._BASE, "api_base_url": ""}
        with pytest.raises(ValidationError, match="api_base_url"):
            ModelItem(**data)

    def test_whitespace_api_base_url_raises(self):
        data = {**self._BASE, "api_base_url": "   "}
        with pytest.raises(ValidationError):
            ModelItem(**data)

    def test_temperature_bounds(self):
        with pytest.raises(ValidationError):
            ModelItem(**{**self._BASE, "temperature": 3.0})  # max=2
        with pytest.raises(ValidationError):
            ModelItem(**{**self._BASE, "temperature": -0.1})  # min=0

    def test_timeout_gt_zero(self):
        with pytest.raises(ValidationError):
            ModelItem(**{**self._BASE, "timeout_seconds": 0})

    def test_max_retries_ge_zero(self):
        with pytest.raises(ValidationError):
            ModelItem(**{**self._BASE, "max_retries": -1})


# ── DataSourceItem ─────────────────────────────────────────────────

class TestDataSourceItem:
    def test_minimal(self):
        item = DataSourceItem(path="/data/source")
        assert item.enabled is True
        assert item.format == "markdown"
        assert item.recursive is True

    def test_disabled(self):
        item = DataSourceItem(path="/data/source", enabled=False)
        assert item.enabled is False


# ── DataSourceConfig validator ─────────────────────────────────────

class TestDataSourceConfigValidator:
    def test_all_disabled_logs_warning_not_raises(self, caplog):
        """全部禁用时应记录 warning，但不应抛出异常。"""
        import logging
        with caplog.at_level(logging.WARNING):
            cfg = DataSourceConfig(
                version="3.0",
                default_source="main",
                sources={"main": DataSourceItem(path="/data", enabled=False)},
            )
        assert isinstance(cfg, DataSourceConfig)
        assert any("禁用" in r.message or "0 个启用" in r.message for r in caplog.records)


# ── EmbeddingConfig ────────────────────────────────────────────────

class TestEmbeddingConfig:
    def test_disabled_by_default(self):
        e = EmbeddingConfig()
        assert e.enabled is False

    def test_timeout_gt_zero(self):
        with pytest.raises(ValidationError):
            EmbeddingConfig(timeout_seconds=0)

    def test_max_retries_ge_zero(self):
        with pytest.raises(ValidationError):
            EmbeddingConfig(max_retries=-1)


# ── WikiConfig ─────────────────────────────────────────────────────

class TestWikiConfig:
    def test_defaults(self):
        w = WikiConfig()
        assert w.version == "3.0"
        assert w.page_types == {}
        assert w.index.auto_update is True
        assert w.changelog.auto_update is True


# ── IngestionConfig ────────────────────────────────────────────────

class TestIngestionConfig:
    def test_defaults(self):
        i = IngestionConfig()
        assert i.scan_on_startup is True
        assert i.max_file_size_mb == 20
        assert i.max_chunk_chars == 1200

    def test_max_file_size_gt_zero(self):
        with pytest.raises(ValidationError):
            IngestionConfig(max_file_size_mb=0)
