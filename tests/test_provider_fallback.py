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


class TestFindModelByNamePrecedence:
    """同名 model 字段下，find_model_by_name 必须按 model_id 精确命中。

    回归背景：该方法原先把**非唯一**的 ``model`` 字段与**唯一**的
    ``model_id`` 同级匹配（``cfg_model == name or model_id == name``），
    命中谁取决于配置书写顺序。真实配置里 ``deepseek-flash-zz``（zz 中转）
    的 ``model`` 字段**就是** ``"deepseek-flash"``（上游模型名与另一条目的
    model_id 同名），且它排在 ``deepseek-flash``（官方直连）前面——于是
    AsrCorrector 的 ``force_model="deepseek-flash"`` 实际打到了 zz 中转，
    而代码注释与启动 banner 都声称「官方直连」，全程没有任何失败信号。
    ``qwen3.8-max`` 同属此类。

    注意复现关键：``-zz`` 条目的 ``model`` 字段必须等于基名（不是随便一个
    共享名），否则旧实现里 ``model_id == model_name`` 那一半会先命中正确
    条目，测不出问题。
    """

    def _make_manager(self, models, default_id="name"):
        from pathlib import Path
        import tempfile
        from iris.llm.model_manager import ModelManager

        cfg = {
            "base_model": {
                "enabled": True,
                "default_model_id": default_id,
                "models": models,
            }
        }
        with tempfile.TemporaryDirectory() as d:
            return ModelManager(cfg, Path(d))

    @staticmethod
    def _entry(model):
        return {"model": model, "provider": "openai",
                "api_key": "sk-x", "api_base_url": "https://api.x.com/v1"}

    def test_zz_first_order_prefers_exact_model_id(self):
        """-zz 条目排前面且 model 字段同名时，查基名必须命中基名条目。"""
        mgr = self._make_manager({
            "name-zz": self._entry("name"),   # 上游模型名 == 另一条的 model_id
            "name": self._entry("name"),
        })
        cfg = mgr.find_model_by_name("name")
        assert cfg is not None
        assert cfg["_model_id"] == "name"

    def test_exact_model_id_wins_regardless_of_order(self):
        """顺序颠倒同样命中基名条目（结果不依赖配置书写顺序）。"""
        mgr = self._make_manager({
            "name": self._entry("name"),
            "name-zz": self._entry("name"),
        })
        cfg = mgr.find_model_by_name("name")
        assert cfg["_model_id"] == "name"

    def test_zz_variant_still_reachable_by_its_model_id(self):
        """-zz 条目仍可通过自身 model_id 命中，不受优先级修复影响。"""
        mgr = self._make_manager({
            "name-zz": self._entry("name"),
            "name": self._entry("name"),
        })
        cfg = mgr.find_model_by_name("name-zz")
        assert cfg["_model_id"] == "name-zz"

    def test_falls_back_to_model_field_when_no_model_id_matches(self):
        """无 model_id 命中时回退按 model 字段匹配，保留既有查找能力。"""
        mgr = self._make_manager({
            "id-x": self._entry("upstream-only"),
        }, default_id="id-x")
        cfg = mgr.find_model_by_name("upstream-only")
        assert cfg is not None
        assert cfg["model"] == "upstream-only"
        assert cfg["_model_id"] == "id-x"


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


# ── 降级链顺序：priority 只在角色内有效 ──────────────────────


class TestFallbackChainOrder:
    """锁死 _build_fallback_chain 的「角色分组」语义。

    回归动机：``models[role].priority`` 只在同一角色内部比较，跨角色顺序由
    ``route_to`` → ``fallback_to`` 的拼接决定。真实 llm.json 中两段区间互不
    重叠且 adv 恒更大（当前 base 65~100 / adv 170~200）。若有人把链实现成
    「合并所有角色后全局按 priority 排序」（看起来更简洁），默认问答会被
    静默升级到 adv_model —— 延迟与成本同时跳档，而既有测试全绿。
    本类用与 llm.json 同构的优先级分布把这条语义钉死。
    """

    @staticmethod
    def _model(priority, *, multimodal=False):
        return {
            "provider": "openai", "model": f"m{priority}",
            "api_base_url": "https://api.test.com/v1", "api_key": "sk-test",
            "priority": priority, "multimodal": multimodal,
            "max_context_tokens": 8192, "timeout_seconds": 10,
        }

    def _models(self):
        """base 65~100 / adv 170~200：adv 数值恒大于 base 最大值。"""
        return {
            "base_model": {
                "enabled": True, "default_model_id": "base-best",
                "models": {
                    "base-best": self._model(100),
                    "base-mid": self._model(95),
                    "base-cheap": self._model(65),
                },
            },
            "adv_model": {
                "enabled": True, "default_model_id": "adv-flagship",
                "models": {
                    "adv-flagship": self._model(200, multimodal=True),
                    "adv-middle": self._model(190, multimodal=True),
                    "adv-cheap": self._model(170, multimodal=True),
                },
            },
        }

    def _make_provider(self, tmp_path, *, models=None, allow_upgrade=True,
                       allow_downgrade=True):
        from iris.config.loader import make_config_bundle
        from iris.llm.provider import EnvironmentConfiguredLLMProvider

        config = make_config_bundle(
            root=tmp_path,
            app={"logging": {"log_to_file": False}, "paths": {"log_dir": "logs"}},
            data_source={},
            llm={
                "version": "3.6",
                "default_strategy": {
                    "default_model_role": "base_model",
                    "fallback_model_role": "adv_model",
                    "prefer_lower_cost": True,
                    "allow_auto_upgrade": allow_upgrade,
                    "allow_auto_downgrade": allow_downgrade,
                },
                "models": models or self._models(),
                "routing": {
                    "rules": [
                        {"name": "qa", "enabled": True, "priority": 1,
                         "match": {"task_type": "qa"}, "route_to": "base_model",
                         "fallback_to": "adv_model"},
                    ]
                },
                "embedding": {"enabled": False, "model": "", "api_base_url": "",
                              "api_key": ""},
            },
        )
        return EnvironmentConfiguredLLMProvider(config)

    @staticmethod
    def _chain(provider, context):
        from iris.llm.router import ModelRouter
        decision = ModelRouter(provider._config).route(context)
        return decision, provider._build_fallback_chain(decision)

    @staticmethod
    def _assert_role_blocks(chain):
        """角色必须成块出现（不交错），且块内 priority 降序。"""
        roles = [role for role, _, _ in chain]
        blocks = [role for i, role in enumerate(roles)
                  if i == 0 or roles[i - 1] != role]
        assert len(blocks) == len(set(blocks)), f"角色块交错: {roles}"
        for role in blocks:
            prios = [cfg.get("priority") for r, _, cfg in chain if r == role]
            assert prios == sorted(prios, reverse=True), \
                f"{role} 块内未按 priority 降序: {prios}"

    def test_chain_grouped_by_role_ignoring_priority_magnitude(self, tmp_path):
        """adv 的 priority 数值更大，也不得排到 base 前面。"""
        provider = self._make_provider(tmp_path)
        decision, chain = self._chain(provider, {"task_type": "qa"})

        assert decision.selected_role == "base_model"
        self._assert_role_blocks(chain)

        roles = [role for role, _, _ in chain]
        assert roles == ["base_model"] * 3 + ["adv_model"] * 3

        base_prios = [cfg.get("priority") for r, _, cfg in chain
                      if r == "base_model"]
        adv_prios = [cfg.get("priority") for r, _, cfg in chain
                     if r == "adv_model"]
        # 数值上 adv 全面更大，顺序却相反 —— 证明数值不参与跨角色比较
        assert max(base_prios) < min(adv_prios)
        assert chain[0][1] == "base-best"

    def test_intra_role_order_is_priority_desc(self, tmp_path):
        """同角色内按 priority 降序，且与配置书写顺序无关。"""
        models = self._models()
        # 打乱书写顺序：cheap → best → mid
        base_models = models["base_model"]["models"]
        models["base_model"]["models"] = {
            "base-cheap": base_models["base-cheap"],
            "base-best": base_models["base-best"],
            "base-mid": base_models["base-mid"],
        }
        provider = self._make_provider(tmp_path, models=models)
        _, chain = self._chain(provider, {"task_type": "qa"})

        base_ids = [mid for role, mid, _ in chain if role == "base_model"]
        assert base_ids == ["base-best", "base-mid", "base-cheap"]

    def test_upgrade_disabled_uses_only_active_model(self, tmp_path):
        """allow_auto_upgrade=false 时主角色只保留活跃模型。"""
        provider = self._make_provider(tmp_path, allow_upgrade=False)
        decision, chain = self._chain(provider, {"task_type": "qa"})

        assert decision.allow_auto_upgrade is False
        base_ids = [mid for role, mid, _ in chain if role == "base_model"]
        assert base_ids == ["base-best"]
        # 跨角色回退仍保留
        assert [role for role, _, _ in chain].count("adv_model") == 3

    def test_downgrade_disabled_drops_fallback_role(self, tmp_path):
        """allow_auto_downgrade=false 时链中不含 fallback 角色。"""
        provider = self._make_provider(tmp_path, allow_downgrade=False)
        decision, chain = self._chain(provider, {"task_type": "qa"})

        assert decision.allow_auto_downgrade is False
        assert {role for role, _, _ in chain} == {"base_model"}

    def test_shipped_example_config_keeps_role_grouping(self, tmp_path):
        """随仓库分发的 llm.json.example 也必须保持角色分组语义。

        llm.json 被 gitignore，CI 里只有 .example，因此这条覆盖的是
        「示例配置被改坏」或「链实现改成全局排序」两类回归。
        """
        import json
        from pathlib import Path

        example = Path(__file__).resolve().parents[1] / "config" / "llm.json.example"
        if not example.exists():
            pytest.skip("缺少 config/llm.json.example")

        from iris.config.loader import make_config_bundle
        from iris.llm.provider import EnvironmentConfiguredLLMProvider
        from iris.llm.router import ModelRouter

        llm = json.loads(example.read_text(encoding="utf-8"))
        bundle = make_config_bundle(
            root=tmp_path,
            app={"logging": {"log_to_file": False}, "paths": {"log_dir": "logs"}},
            data_source={},
            llm=llm,
        )
        provider = EnvironmentConfiguredLLMProvider(bundle)
        router = ModelRouter(bundle)

        contexts = [
            {"task_type": "qa"},
            {"input_type": "multimodal"},
            {"task_type": "wiki_rebuild"},
        ]
        for ctx in contexts:
            decision = router.route(ctx)
            chain = provider._build_fallback_chain(decision)
            assert chain, f"{ctx} 的降级链为空"
            assert chain[0][0] == decision.selected_role
            self._assert_role_blocks(chain)
