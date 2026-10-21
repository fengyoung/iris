"""测试 Anthropic 多模态 API 支持。"""

import pytest
from unittest.mock import patch

from iris.llm.provider import EnvironmentConfiguredLLMProvider, LLMProviderError
from iris.config.loader import make_config_bundle


class TestAnthropicMultimodal:
    """测试 Anthropic 多模态支持。"""

    @pytest.fixture
    def provider(self, tmp_path):
        """创建带 Anthropic 模型的 provider。"""
        config = make_config_bundle(
            root=tmp_path,
            app={"logging": {"log_to_file": False}, "paths": {"log_dir": "logs"}},
            data_source={},
            llm={
                "version": "3.6",
                "default_strategy": {
                    "default_model_role": "adv_model",
                    "fallback_model_role": "adv_model",  # 修复：不能为 None
                    "allow_auto_upgrade": True,
                    "allow_auto_downgrade": True,
                },
                "models": {
                    "adv_model": {
                        "enabled": True,
                        "default_model_id": "claude-sonnet-4",
                        "models": {
                            "claude-sonnet-4": {
                                "provider": "anthropic",
                                "model": "claude-sonnet-4-20250514",
                                "api_base_url": "https://api.anthropic.com",
                                "api_key": "test-key",
                                "multimodal": True,
                                "max_tokens": 8192,
                                "timeout_seconds": 120,
                                "max_retries": 2,
                                "priority": 100,
                                "display_name": "Claude Sonnet 4",
                                "max_context_tokens": 200000,
                                "temperature": 0.2,
                                "cost_level": "high",
                                "reasoning_level": "advanced",
                                "supported_inputs": ["text", "image"],
                                "use_cases": ["qa"],
                                "notes": "Test model",
                            }
                        }
                    }
                },
                "routing": {"rules": []},
                "embedding": {"enabled": False, "model": "", "api_base_url": "", "api_key": ""},
            },
        )
        return EnvironmentConfiguredLLMProvider(config)

    def test_anthropic_multimodal_converts_image_url_format(self, provider):
        """测试 Anthropic 多模态正确转换 OpenAI 格式的图片 URL。"""
        # 模拟 OpenAI 格式的 content_parts
        content_parts = [
            {"type": "text", "text": "描述这张图片"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="}
            }
        ]

        # 模拟 HTTP 响应
        mock_response = {
            "content": [{"type": "text", "text": "这是一张图片"}],
            "usage": {"input_tokens": 100, "output_tokens": 50}
        }

        with patch.object(provider, '_post_json', return_value=mock_response) as mock_post:
            result = provider._call_anthropic_multimodal(
                "https://api.anthropic.com",
                "test-key",
                "claude-sonnet-4-20250514",
                content_parts,
                max_tokens=8192,
            )

            # 验证返回值
            assert result[0] == "这是一张图片"
            assert result[1] == 100  # prompt_tokens
            assert result[2] == 50   # completion_tokens

            # 验证请求格式转换正确
            call_args = mock_post.call_args
            payload = call_args[0][1]

            # 验证 messages 结构
            assert "messages" in payload
            assert len(payload["messages"]) == 1
            assert payload["messages"][0]["role"] == "user"

            # 验证 content 转换为 Anthropic 格式
            content = payload["messages"][0]["content"]
            assert len(content) == 2
            assert content[0] == {"type": "text", "text": "描述这张图片"}
            assert content[1]["type"] == "image"
            assert content[1]["source"]["type"] == "base64"
            assert content[1]["source"]["media_type"] == "image/jpeg"
            assert content[1]["source"]["data"] == "/9j/4AAQSkZJRg=="

    def test_anthropic_multimodal_handles_text_only(self, provider):
        """测试 Anthropic 多模态处理纯文本内容。"""
        content_parts = [
            {"type": "text", "text": "你好"},
        ]

        mock_response = {
            "content": [{"type": "text", "text": "你好！"}],
            "usage": {"input_tokens": 10, "output_tokens": 5}
        }

        with patch.object(provider, '_post_json', return_value=mock_response) as mock_post:
            result = provider._call_anthropic_multimodal(
                "https://api.anthropic.com",
                "test-key",
                "claude-sonnet-4-20250514",
                content_parts,
                max_tokens=8192,
            )

            assert result[0] == "你好！"

            # 验证 content 格式
            payload = mock_post.call_args[0][1]
            content = payload["messages"][0]["content"]
            assert len(content) == 1
            assert content[0] == {"type": "text", "text": "你好"}

    def test_anthropic_multimodal_raises_on_invalid_image_url(self, provider):
        """测试 Anthropic 多模态对无效图片 URL 抛出错误。"""
        content_parts = [
            {"type": "text", "text": "描述这张图片"},
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/image.jpg"}
            }
        ]

        with pytest.raises(LLMProviderError, match="当前仅支持 base64 编码的图片"):
            provider._call_anthropic_multimodal(
                "https://api.anthropic.com",
                "test-key",
                "claude-sonnet-4-20250514",
                content_parts,
                max_tokens=8192,
            )

    def test_anthropic_multimodal_raises_on_malformed_data_uri(self, provider):
        """测试 Anthropic 多模态对格式错误的 data URI 抛出错误。"""
        content_parts = [
            {
                "type": "image_url",
                "image_url": {"url": "data:invalid"}
            }
        ]

        with pytest.raises(LLMProviderError, match="无法解析图片 data URI"):
            provider._call_anthropic_multimodal(
                "https://api.anthropic.com",
                "test-key",
                "claude-sonnet-4-20250514",
                content_parts,
                max_tokens=8192,
            )


class TestAnthropicInputTokenAccounting:
    """Anthropic usage 的输入 token 口径：须含缓存读写，与 OpenAI prompt_tokens 对齐。

    Anthropic 的 input_tokens 只是「未缓存余量」，缓存部分另计。漏计不会报错，
    只会让用量库少记——实测经中转的 claude-opus-5-5 单次 cache_read 可达 5.7 万。
    """

    def test_sums_cache_read_into_input_tokens(self):
        from iris.llm.provider import _anthropic_input_tokens

        # 真实抓包形态：input 2961 + cache_read 57672 = 60633
        usage = {
            "input_tokens": 2961,
            "output_tokens": 3,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 57672,
        }
        assert _anthropic_input_tokens(usage) == 60633

    def test_sums_cache_creation_into_input_tokens(self):
        from iris.llm.provider import _anthropic_input_tokens

        usage = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 2048,
            "cache_read_input_tokens": 0,
        }
        assert _anthropic_input_tokens(usage) == 2148

    def test_plain_usage_without_cache_fields_unchanged(self):
        """无缓存字段时须与旧行为一致，不能因求和引入偏差。"""
        from iris.llm.provider import _anthropic_input_tokens

        assert _anthropic_input_tokens({"input_tokens": 16, "output_tokens": 28}) == 16

    def test_null_cache_fields_treated_as_zero(self):
        """中转可能回传 null —— int(None) 会抛 TypeError，须按 0 处理。"""
        from iris.llm.provider import _anthropic_input_tokens

        usage = {
            "input_tokens": 19,
            "cache_read_input_tokens": None,
            "cache_creation_input_tokens": None,
        }
        assert _anthropic_input_tokens(usage) == 19

    def test_empty_usage_returns_zero(self):
        from iris.llm.provider import _anthropic_input_tokens

        assert _anthropic_input_tokens({}) == 0

    def test_call_anthropic_reports_cache_inclusive_tokens(self, tmp_path):
        """端到端：_call_anthropic 返回的 prompt_tokens 须为含缓存的总输入。"""
        from iris.config.loader import make_config_bundle
        from iris.llm.provider import EnvironmentConfiguredLLMProvider

        bundle = make_config_bundle(
            root=tmp_path,
            app={"logging": {"log_to_file": False}, "paths": {"log_dir": "logs"}},
            data_source={},
            llm={
                "version": "3.6",
                "default_strategy": {
                    "default_model_role": "adv_model",
                    "fallback_model_role": "adv_model",
                    "allow_auto_upgrade": True,
                    "allow_auto_downgrade": True,
                },
                "models": {
                    "adv_model": {
                        "enabled": True,
                        "default_model_id": "claude-test",
                        "models": {
                            "claude-test": {
                                "channel": "test",
                                "model": "claude-test-model",
                                "provider": "anthropic",
                                "api_base_url": "https://example.invalid",
                                "api_key": "k",
                                "max_tokens": 1024,
                                "priority": 100,
                            }
                        },
                    }
                },
                "routing": {"rules": []},
            },
        )
        provider = EnvironmentConfiguredLLMProvider(bundle)

        fake_response = {
            "content": [{"type": "text", "text": "收到"}],
            "usage": {
                "input_tokens": 2961,
                "output_tokens": 3,
                "cache_read_input_tokens": 57672,
                "cache_creation_input_tokens": 0,
            },
        }
        with patch.object(provider, "_post_json", return_value=fake_response):
            text, pt, ct = provider._call_anthropic(
                "https://example.invalid", "k", "claude-test-model", "hi",
                max_tokens=1024,
            )
        assert text == "收到"
        assert pt == 60633, "prompt_tokens 必须含 cache_read，否则用量少记 95%"
        assert ct == 3
