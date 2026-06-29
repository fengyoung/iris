"""H3 降级链 + H1 LLM retry 专项测试。"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from iris.llm.provider import LLMProviderError, LLMRequest, LLMResponse
from iris.llm.router import RoutingDecision


# ── _fallback_loop 行为测试 ──────────────────────────────────

class TestFallbackLoop:
    """验证 _fallback_loop 的降级行为和错误处理。"""

    MODELS = {
        "base_model": {
            "enabled": True,
            "default_model_id": "base-1",
            "models": {
                "base-1": {
                    "provider": "openai", "model": "base-1", "display_name": "B1",
                    "multimodal": False, "max_context_tokens": 4096, "temperature": 0.2,
                    "timeout_seconds": 10, "max_retries": 0, "priority": 10,
                    "cost_level": "low", "reasoning_level": "standard",
                    "supported_inputs": ["text"], "use_cases": ["qa"], "notes": "",
                    "api_base_url": "https://api.test.com/v1", "api_key": "sk-b1",
                },
                "base-2": {
                    "provider": "openai", "model": "base-2", "display_name": "B2",
                    "multimodal": False, "max_context_tokens": 4096, "temperature": 0.2,
                    "timeout_seconds": 10, "max_retries": 0, "priority": 5,
                    "cost_level": "low", "reasoning_level": "advanced",
                    "supported_inputs": ["text"], "use_cases": ["qa"], "notes": "",
                    "api_base_url": "https://api.test.com/v1", "api_key": "sk-b2",
                },
            },
        },
        "adv_model": {
            "enabled": True,
            "default_model_id": "adv-1",
            "models": {
                "adv-1": {
                    "provider": "openai", "model": "adv-1", "display_name": "A1",
                    "multimodal": True, "max_context_tokens": 8192, "temperature": 0.2,
                    "timeout_seconds": 30, "max_retries": 0, "priority": 10,
                    "cost_level": "medium", "reasoning_level": "advanced",
                    "supported_inputs": ["text", "image"], "use_cases": ["qa"], "notes": "",
                    "api_base_url": "https://api.test.com/v1", "api_key": "sk-adv",
                },
            },
        },
    }

    def _make_provider(self, tmp_path):
        from iris.llm.provider import EnvironmentConfiguredLLMProvider
        from iris.config.loader import ConfigBundle

        config = ConfigBundle(
            root=Path("/tmp"),
            app={"logging": {"log_to_file": False}, "paths": {"log_dir": "logs"}},
            data_source={},
            llm={
                "version": "3.3",
                "default_strategy": {
                    "default_model_role": "base_model",
                    "fallback_model_role": "adv_model",
                    "prefer_lower_cost": True,
                    "allow_auto_upgrade": True,
                    "allow_auto_downgrade": True,
                },
                "models": self.MODELS,
                "routing": {
                    "rules": [
                        {"name": "test-rule", "enabled": True, "priority": 1,
                         "match": {"task_type": "qa"}, "route_to": "base_model",
                         "fallback_to": "adv_model"},
                    ]
                },
                "embedding": {"enabled": False, "model": "", "api_base_url": "", "api_key": ""},
            },
        )
        return EnvironmentConfiguredLLMProvider(config)

    def test_fallback_loop_succeeds_on_first_model(self, tmp_path):
        """降级链：第一个模型成功即返回。"""
        provider = self._make_provider(tmp_path)
        decision = RoutingDecision(
            selected_role="base_model", fallback_role="adv_model",
            matched_rule="test", allow_auto_upgrade=True, allow_auto_downgrade=True,
        )

        call_count = [0]

        def call_fn(api_base, api_key, model_name, cfg):
            call_count[0] += 1
            return f"response-from-{model_name}"

        text, role, provider_name, model_name, api_base = provider._fallback_loop(
            decision, call_fn,
        )

        assert text == "response-from-base-1"
        assert role == "base_model"
        assert call_count[0] == 1  # 第一次就成功

    def test_fallback_loop_retries_on_failure(self, tmp_path):
        """降级链：第一个失败后尝试下一个。"""
        provider = self._make_provider(tmp_path)
        decision = RoutingDecision(
            selected_role="base_model", fallback_role="adv_model",
            matched_rule="test", allow_auto_upgrade=True, allow_auto_downgrade=True,
        )

        call_count = [0]

        def call_fn(api_base, api_key, model_name, cfg):
            call_count[0] += 1
            if model_name == "base-1":
                raise LLMProviderError("base-1 挂了")
            return f"response-from-{model_name}"

        text, role, provider_name, model_name, api_base = provider._fallback_loop(
            decision, call_fn,
        )

        assert text == "response-from-base-2"
        assert call_count[0] == 2  # 第一次失败，第二次成功

    def test_fallback_loop_cross_role_fallback(self, tmp_path):
        """降级链：同角色全部失败后跨角色降级。"""
        provider = self._make_provider(tmp_path)
        decision = RoutingDecision(
            selected_role="base_model", fallback_role="adv_model",
            matched_rule="test", allow_auto_upgrade=True, allow_auto_downgrade=True,
        )

        call_count = [0]

        def call_fn(api_base, api_key, model_name, cfg):
            call_count[0] += 1
            if model_name in ("base-1", "base-2"):
                raise LLMProviderError(f"{model_name} 挂了")
            return f"response-from-{model_name}"

        text, role, provider_name, model_name, api_base = provider._fallback_loop(
            decision, call_fn,
        )

        assert text == "response-from-adv-1"
        assert role == "adv_model"
        assert call_count[0] == 3  # base-1, base-2 失败，adv-1 成功

    def test_fallback_loop_all_failed_raises(self, tmp_path):
        """降级链全部失败时抛出 LLMProviderError。"""
        provider = self._make_provider(tmp_path)
        decision = RoutingDecision(
            selected_role="base_model", fallback_role="adv_model",
            matched_rule="test", allow_auto_upgrade=True, allow_auto_downgrade=True,
        )

        def call_fn(api_base, api_key, model_name, cfg):
            raise LLMProviderError(f"{model_name} 挂了")

        with pytest.raises(LLMProviderError, match="全部降级链"):
            provider._fallback_loop(decision, call_fn)

    def test_multimodal_filter_skips_text_only_models(self, tmp_path):
        """多模态调用自动跳过 multimodal=false 的模型。"""
        provider = self._make_provider(tmp_path)
        decision = RoutingDecision(
            selected_role="base_model", fallback_role="adv_model",
            matched_rule="test", allow_auto_upgrade=True, allow_auto_downgrade=True,
        )

        call_count = [0]

        def call_fn(api_base, api_key, model_name, cfg):
            call_count[0] += 1
            return f"response-from-{model_name}"

        text, role, provider_name, model_name, api_base = provider._fallback_loop(
            decision, call_fn,
            model_filter=lambda cfg: cfg.get("multimodal", False),
        )

        # base-1 和 base-2 都是 multimodal=false，被跳过
        # adv-1 是 multimodal=true，被调用
        assert model_name == "adv-1"
        assert call_count[0] == 1

    def test_model_without_api_key_skipped(self, tmp_path):
        """没有 api_key 的模型被跳过。"""
        models_no_key = dict(self.MODELS)
        models_no_key["base_model"]["models"]["base-1"]["api_key"] = ""
        # 确保 base-2 有 key
        models_no_key["base_model"]["models"]["base-2"]["api_key"] = "sk-ok"

        provider = self._make_provider(tmp_path)
        provider._model_manager = provider._model_manager.__class__(
            models_no_key, tmp_path
        )
        decision = RoutingDecision(
            selected_role="base_model", fallback_role=None,
            matched_rule="test", allow_auto_upgrade=True, allow_auto_downgrade=False,
        )

        call_count = [0]

        def call_fn(api_base, api_key, model_name, cfg):
            call_count[0] += 1
            return f"response-from-{model_name}"

        text, role, provider_name, model_name, api_base = provider._fallback_loop(
            decision, call_fn,
        )

        # base-1(无key) 被跳过 → base-2(有key) 成功
        assert model_name == "base-2"
        assert call_count[0] == 1

    def test_generate_respects_max_retries_override(self, tmp_path):
        """generate() 接受 max_retries 覆盖参数。"""
        provider = self._make_provider(tmp_path)

        with patch.object(provider, '_call_openai_compatible', return_value="ok"):
            response = provider.generate(
                LLMRequest(prompt="test", route_context={"task_type": "qa"}),
                max_retries=5,
            )
            # 验证调用成功
            assert response.text == "ok"

    def test_generate_multimodal_respects_max_retries_override(self, tmp_path):
        """generate_multimodal() 接受 max_retries 覆盖参数。"""
        provider = self._make_provider(tmp_path)

        with patch.object(provider, '_call_openai_compatible_multimodal', return_value="ok"):
            text = provider.generate_multimodal(
                [{"type": "text", "text": "test"}],
                {"task_type": "qa"},
                max_retries=3,
            )
            assert text == "ok"
