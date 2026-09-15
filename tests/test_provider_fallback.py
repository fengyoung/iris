"""H3 降级链 + H1 LLM retry 专项测试。"""

import pytest
from pathlib import Path
from unittest.mock import patch

from iris.llm.provider import LLMProviderError, LLMRequest
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
        from iris.config.loader import make_config_bundle

        config = make_config_bundle(
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
            return f"response-from-{model_name}", 0, 0

        text, role, provider_name, model_name, api_base, pt, ct = provider._fallback_loop(
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
            return f"response-from-{model_name}", 0, 0

        text, role, provider_name, model_name, api_base, pt, ct = provider._fallback_loop(
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
            return f"response-from-{model_name}", 0, 0

        text, role, provider_name, model_name, api_base, pt, ct = provider._fallback_loop(
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
            return f"response-from-{model_name}", 0, 0

        text, role, provider_name, model_name, api_base, pt, ct = provider._fallback_loop(
            decision, call_fn,
            model_filter=lambda cfg: cfg.get("multimodal", False),
        )

        # base-1 和 base-2 都是 multimodal=false，被跳过
        # adv-1 是 multimodal=true，被调用
        assert model_name == "adv-1"
        assert call_count[0] == 1

    def test_model_without_api_key_skipped(self, tmp_path):
        """没有 api_key 的模型被跳过。"""
        # deepcopy：dict(self.MODELS) 只拷贝顶层，嵌套的 models 字典仍是共享引用，
        # 会污染类级 self.MODELS，导致后续测试（顺序敏感）读到被清空的 api_key。
        import copy
        models_no_key = copy.deepcopy(self.MODELS)
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
            return f"response-from-{model_name}", 0, 0

        text, role, provider_name, model_name, api_base, pt, ct = provider._fallback_loop(
            decision, call_fn,
        )

        # base-1(无key) 被跳过 → base-2(有key) 成功
        assert model_name == "base-2"
        assert call_count[0] == 1

    def test_generate_respects_max_retries_override(self, tmp_path):
        """generate() 接受 max_retries 覆盖参数。"""
        provider = self._make_provider(tmp_path)

        with patch.object(provider, '_call_openai_compatible', return_value=("ok", 10, 5)):
            response = provider.generate(
                LLMRequest(prompt="test", route_context={"task_type": "qa"}),
                max_retries=5,
            )
            # 验证调用成功
            assert response.text == "ok"

    def test_generate_multimodal_respects_max_retries_override(self, tmp_path):
        """generate_multimodal() 接受 max_retries 覆盖参数。"""
        provider = self._make_provider(tmp_path)

        with patch.object(provider, '_call_openai_compatible_multimodal', return_value=("ok", 20, 8)):
            text = provider.generate_multimodal(
                [{"type": "text", "text": "test"}],
                {"task_type": "qa"},
                max_retries=3,
            )
            assert text == "ok"

    def test_generate_as_calls_exact_model(self, tmp_path):
        """generate_as 应精确调用 (role, model_id)，不走路由/降级链。"""
        provider = self._make_provider(tmp_path)

        with patch.object(provider, '_call_openai_compatible', return_value=("exact-ok", 3, 4)) as mock_call:
            response = provider.generate_as(
                "base_model", "base-2",
                LLMRequest(prompt="hi", route_context={"task_type": "qa"}),
            )
            assert response.text == "exact-ok"
            assert response.model == "base-2"
            assert response.matched_rule == "exact_model"
            # 确认调用的是 base-2 而非默认的 base-1（default_model_id）
            called_model_name = mock_call.call_args[0][2]
            assert called_model_name == "base-2"

    def test_generate_as_missing_model_raises(self, tmp_path):
        """generate_as 指定不存在的 model_id 应抛出 LLMProviderError。"""
        provider = self._make_provider(tmp_path)
        with pytest.raises(LLMProviderError):
            provider.generate_as(
                "base_model", "no-such-model",
                LLMRequest(prompt="hi", route_context={"task_type": "qa"}),
            )

    def test_generate_as_no_fallback_on_failure(self, tmp_path):
        """generate_as 调用失败时不应降级到其他模型，直接抛错。"""
        provider = self._make_provider(tmp_path)
        with patch.object(provider, '_call_openai_compatible', side_effect=LLMProviderError("boom")):
            with pytest.raises(LLMProviderError, match="boom"):
                provider.generate_as(
                    "base_model", "base-1",
                    LLMRequest(prompt="hi", route_context={"task_type": "qa"}),
                )

    def test_generate_multimodal_as_calls_exact_model(self, tmp_path):
        """generate_multimodal_as 应精确调用指定模型的多模态接口。"""
        provider = self._make_provider(tmp_path)

        with patch.object(provider, '_call_openai_compatible_multimodal', return_value=("desc", 5, 6)) as mock_call:
            text = provider.generate_multimodal_as(
                "adv_model", "adv-1",
                [{"type": "text", "text": "describe"}],
                {"task_type": "undercover_describe"},
            )
            assert text == "desc"
            mock_call.assert_called_once()

    def test_generate_multimodal_as_rejects_non_multimodal_model(self, tmp_path):
        """generate_multimodal_as 对不支持多模态的模型应直接拒绝。"""
        provider = self._make_provider(tmp_path)
        # base-1/base-2 在测试夹具中 multimodal=False
        with pytest.raises(LLMProviderError, match="不支持多模态"):
            provider.generate_multimodal_as(
                "base_model", "base-1",
                [{"type": "text", "text": "x"}],
                {"task_type": "qa"},
            )


# ── ModelManager.find_model_by_name ──────────────────────────────────

class TestFindModelByName:
    """Q2: ModelManager.find_model_by_name 公开封装方法测试。"""

    def _make_manager(self):
        from pathlib import Path
        import tempfile
        from iris.llm.model_manager import ModelManager
        models = {
            "base_model": {
                "enabled": True,
                "default_model_id": "m1",
                "models": {
                    "m1": {"model": "deepseek-chat", "provider": "deepseek",
                           "api_key": "sk-x", "api_base_url": "https://api.x.com/v1",
                           "timeout_seconds": 60, "max_retries": 0},
                },
            },
            "adv_model": {
                "enabled": True,
                "default_model_id": "m2",
                "models": {
                    "m2": {"model": "qwen-vl", "provider": "qwen",
                           "api_key": "sk-y", "api_base_url": "https://api.y.com/v1",
                           "timeout_seconds": 60, "max_retries": 0},
                },
            },
        }
        with tempfile.TemporaryDirectory() as d:
            return ModelManager(models, Path(d))

    def test_find_by_model_name(self):
        mgr = self._make_manager()
        cfg = mgr.find_model_by_name("deepseek-chat")
        assert cfg is not None
        assert cfg["model"] == "deepseek-chat"

    def test_find_by_model_id(self):
        mgr = self._make_manager()
        cfg = mgr.find_model_by_name("m2")
        assert cfg is not None
        assert cfg["model"] == "qwen-vl"

    def test_find_nonexistent_returns_none(self):
        mgr = self._make_manager()
        assert mgr.find_model_by_name("no-such-model") is None

    def test_find_includes_api_key(self):
        """返回的配置应包含 api_key（用于 provider 内部调用）。"""
        mgr = self._make_manager()
        cfg = mgr.find_model_by_name("deepseek-chat")
        assert "api_key" in cfg
        assert cfg["api_key"] == "sk-x"

    def test_find_includes_model_id(self):
        """返回的配置应附带 _model_id 字段。"""
        mgr = self._make_manager()
        cfg = mgr.find_model_by_name("deepseek-chat")
        assert "_model_id" in cfg
        assert cfg["_model_id"] == "m1"

    def test_find_with_pydantic_config(self):
        """真实配置路径：Pydantic RoleModels/ModelItem 对象也应可查找。

        回归：v3.11 迁移 Pydantic 后 isinstance(dict) 门控导致 force_model
        对真实配置（load_config_bundle）永远返回 None。
        """
        from pathlib import Path
        import tempfile
        from iris.config.models import ModelItem, RoleModels
        from iris.llm.model_manager import ModelManager

        models = {
            "adv_model": RoleModels(
                enabled=True,
                default_model_id="m2",
                models={
                    "m2": ModelItem(
                        provider="deepseek",
                        model="deepseek-v4-flash-vision-exp",
                        api_base_url="https://api.deepseek.com/v1",
                        api_key="sk-vision",
                    ),
                },
            ),
        }
        with tempfile.TemporaryDirectory() as d:
            mgr = ModelManager(models, Path(d))
            cfg = mgr.find_model_by_name("deepseek-v4-flash-vision-exp")
            assert cfg is not None
            assert cfg["model"] == "deepseek-v4-flash-vision-exp"
            assert cfg["api_key"] == "sk-vision"  # SecretStr 已解包
            assert cfg["_model_id"] == "m2"



# ── 响应文本提取（_extract_chat_completions_text）────────────────

class TestExtractChatCompletionsText:
    """响应文本提取：content 为空时绝不回退 reasoning_content（v3.28.1 修复）。

    回归背景：DeepSeek 思考模型在 max_tokens 耗尽等场景下 content 为空但
    reasoning_content 非空，旧实现静默回退返回思考过程，导致某期双周报
    Stage 4b 审查输出被思考文本污染写入最终产物。
    """

    def test_normal_string_content(self):
        from iris.llm.provider import _extract_chat_completions_text
        payload = {"choices": [{"message": {"content": "  报告正文  "}}]}
        assert _extract_chat_completions_text(payload) == "报告正文"

    def test_empty_string_content_with_reasoning_raises(self):
        """content 为空 + reasoning_content 非空 → 抛 LLMProviderError，而非返回思考。"""
        from iris.llm.provider import _extract_chat_completions_text
        payload = {
            "choices": [{
                "message": {"content": "", "reasoning_content": "思考过程"},
                "finish_reason": "length",
            }]
        }
        with pytest.raises(LLMProviderError, match="未找到可用文本输出"):
            _extract_chat_completions_text(payload)

    def test_missing_content_with_reasoning_raises(self):
        """content 字段缺失 + reasoning_content 非空 → 同样抛错。"""
        from iris.llm.provider import _extract_chat_completions_text
        payload = {
            "choices": [{
                "message": {"reasoning_content": "思考过程"},
                "finish_reason": "stop",
            }]
        }
        with pytest.raises(LLMProviderError, match="未找到可用文本输出"):
            _extract_chat_completions_text(payload)

    def test_list_content_extracts_text_parts(self):
        """多模态 content 列表正常提取文本片段。"""
        from iris.llm.provider import _extract_chat_completions_text
        payload = {
            "choices": [{"message": {"content": [
                {"type": "text", "text": "片段一"},
                {"type": "text", "text": "片段二"},
            ]}}]
        }
        assert _extract_chat_completions_text(payload) == "片段一\n片段二"

    def test_empty_list_content_with_reasoning_raises(self):
        """content 列表无文本 + reasoning_content 非空 → 抛错。"""
        from iris.llm.provider import _extract_chat_completions_text
        payload = {
            "choices": [{"message": {
                "content": [{"type": "text", "text": ""}],
                "reasoning_content": "思考过程",
            }}]
        }
        with pytest.raises(LLMProviderError, match="未找到可用文本输出"):
            _extract_chat_completions_text(payload)


# ── ModelManager.get_model_config 测试（精确查询，避免歧义）──

class TestModelManagerGetModelConfig:
    """测试 ModelManager.get_model_config 精确按 (role, model_id) 查找。"""

    def _make_manager(self, models_dict):
        from pathlib import Path
        import tempfile
        from iris.llm.model_manager import ModelManager
        with tempfile.TemporaryDirectory() as d:
            return ModelManager(models_dict, Path(d))

    def test_get_model_config_exact_match(self):
        """正常查找应返回完整配置含 api_key。"""
        models_dict = {
            "base_model": {
                "enabled": True,
                "default_model_id": "test-model",
                "models": {
                    "test-model": {
                        "provider": "openai", "model": "test",
                        "api_base_url": "http://localhost", "api_key": "secret123",
                    }
                }
            }
        }
        mgr = self._make_manager(models_dict)
        cfg = mgr.get_model_config("base_model", "test-model")
        assert cfg["model"] == "test"
        assert cfg["api_key"] == "secret123"
        assert cfg["_model_id"] == "test-model"

    def test_get_model_config_no_name_collision(self):
        """两个 model_id 的 model 字段相同时，get_model_config 能精确区分。

        回归动机：find_model_by_name 按 model 字段字符串匹配，当两个
        model_id 复用同一 model 字段时（如 llm.json 中 deepseek-flash-zz
        与 deepseek-flash 的 model 字段都是 "deepseek-flash"）会产生歧义。
        get_model_config 按 (role, model_id) 精确定位，消除歧义。
        """
        models_dict = {
            "base_model": {
                "enabled": True,
                "default_model_id": "id-a",
                "models": {
                    "id-a": {
                        "provider": "openai", "model": "shared-name",
                        "api_base_url": "http://a", "api_key": "key-a",
                    },
                    "id-b": {
                        "provider": "openai", "model": "shared-name",
                        "api_base_url": "http://b", "api_key": "key-b",
                    },
                }
            }
        }
        mgr = self._make_manager(models_dict)

        cfg_a = mgr.get_model_config("base_model", "id-a")
        assert cfg_a["api_base_url"] == "http://a"
        assert cfg_a["api_key"] == "key-a"

        cfg_b = mgr.get_model_config("base_model", "id-b")
        assert cfg_b["api_base_url"] == "http://b"
        assert cfg_b["api_key"] == "key-b"

    def test_get_model_config_unknown_role_raises(self):
        """role 不存在时抛出 ModelManagerError。"""
        from iris.llm.model_manager import ModelManagerError
        mgr = self._make_manager({})
        with pytest.raises(ModelManagerError, match="未知角色"):
            mgr.get_model_config("unknown_role", "any-id")

    def test_get_model_config_unknown_model_id_raises(self):
        """model_id 不存在时抛出 ModelManagerError。"""
        from iris.llm.model_manager import ModelManagerError
        models_dict = {
            "base_model": {
                "enabled": True,
                "default_model_id": "existing",
                "models": {
                    "existing": {
                        "provider": "openai", "model": "m",
                        "api_base_url": "http://x", "api_key": "k",
                    }
                }
            }
        }
        mgr = self._make_manager(models_dict)
        with pytest.raises(ModelManagerError, match="未找到模型"):
            mgr.get_model_config("base_model", "nonexistent")
