"""Pydantic v2 配置模型测试（从 iris2 迁移）。"""

import pytest
from pathlib import Path

from iris.config.models import (
    ConfigBundleV2, AppConfig, QAConfig, LLMConfig, ModelItem,
    RoleModels, DefaultStrategy, RoutingRule, RoutingConfig,
    WikiConfig, PageTypeConfig, DataSourceConfig, DataSourceItem,
)


class TestQAConfig:
    def test_defaults(self):
        qa = QAConfig()
        assert qa.max_prompt_context_chars == 6000
        assert qa.max_evidence_blocks == 6
        assert qa.max_wiki_hits == 3

    def test_rejects_negative_values(self):
        with pytest.raises(Exception):
            QAConfig(max_prompt_context_chars=-1)

    def test_rejects_zero(self):
        with pytest.raises(Exception):
            QAConfig(max_prompt_context_chars=0)


class TestModelItem:
    def test_valid_model(self):
        m = ModelItem(
            provider="openai", model="gpt-test",
            supported_inputs=["text"], use_cases=["qa"],
            api_base_url="https://api.test.com/v1", api_key="sk-test",
        )
        assert m.multimodal is False
        assert m.max_context_tokens == 4096
        assert m.timeout_seconds == 60

    def test_rejects_empty_api_base_url(self):
        with pytest.raises(ValueError, match="api_base_url"):
            ModelItem(
                provider="openai", model="gpt-test",
                supported_inputs=["text"], use_cases=["qa"],
                api_base_url="", api_key="sk-test",
            )

    def test_temperature_range(self):
        with pytest.raises(Exception):
            ModelItem(
                provider="openai", model="gpt-test",
                supported_inputs=["text"], use_cases=["qa"],
                api_base_url="https://api.test.com/v1", api_key="sk-test",
                temperature=3.0,  # > 2
            )

    def test_reasoning_level_literal(self):
        """reasoning_level 只能是 standard 或 advanced。"""
        m = ModelItem(
            provider="openai", model="gpt-test",
            supported_inputs=["text"], use_cases=["qa"],
            api_base_url="https://api.test.com/v1", api_key="sk-test",
            reasoning_level="advanced",
        )
        assert m.reasoning_level == "advanced"

    def test_multimodal_model(self):
        m = ModelItem(
            provider="bailian", model="qwen-vl",
            supported_inputs=["text", "image"], use_cases=["qa", "image_understanding"],
            api_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="sk-test", multimodal=True,
        )
        assert m.multimodal is True
        assert "image" in m.supported_inputs


class TestConfigBundleV2:
    def test_from_config_bundle(self):
        """验证从真实 ConfigBundle 转换。"""
        import sys
        sys.path.insert(0, 'src')
        from iris.config.loader import load_config_bundle

        bundle = load_config_bundle(Path.cwd())
        v2 = ConfigBundleV2.from_config_bundle(bundle)

        # 基本字段非空
        assert v2.root is not None
        assert v2.app is not None
        assert v2.llm is not None
        assert v2.wiki is not None

        # LLM 配置正确
        assert v2.llm.version == "3.4"
        assert len(v2.llm.models) >= 2
        assert v2.llm.models["adv_model"].default_model_id == "qwen3.6-plus"
        assert v2.llm.default_strategy.allow_auto_upgrade is True

        # 路由规则（v3.8 新增 prompt_gen_go_base → 8 条）
        assert len(v2.llm.routing.rules) == 8
        # 验证新增规则
        rule_names = [r.name for r in v2.llm.routing.rules]
        assert "prompt_gen_go_base" in rule_names

        # Wiki 配置
        assert len(v2.wiki.page_types) == 4
        assert "person" in v2.wiki.page_types

    def test_wiki_config_defaults(self):
        wiki = WikiConfig(
            version="3.2",
            wiki_root="/tmp/wiki",
            page_types={
                "domain": PageTypeConfig(subdir="01-领域", filename_prefix="领域-", template_name="domain.md"),
            },
        )
        assert wiki.index.auto_update is True
        assert wiki.changelog.filename == "changelog.md"


class TestDataSourceValidation:
    def test_rejects_no_enabled_sources(self):
        with pytest.raises(ValueError, match="至少需要启用一个数据源"):
            DataSourceConfig(
                version="3.2",
                default_source="test",
                sources={
                    "test": DataSourceItem(enabled=False, path="/tmp/test"),
                },
            )
