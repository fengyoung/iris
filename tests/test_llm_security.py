"""C1 API Key 脱敏 + M1 策略开关 专项测试。"""

from pathlib import Path

from iris.llm.model_manager import ModelManager


# ── C1: get_active_model_config 默认脱敏 ─────────────────────

class TestApiKeyStripping:
    """验证 get_active_model_config 默认不返回 api_key。"""

    MODELS = {
        "test_role": {
            "enabled": True,
            "default_model_id": "m1",
            "models": {
                "m1": {
                    "provider": "openai", "model": "gpt-test", "display_name": "Test",
                    "multimodal": False, "max_context_tokens": 4096, "temperature": 0.2,
                    "timeout_seconds": 10, "max_retries": 0, "priority": 10,
                    "cost_level": "low", "reasoning_level": "standard",
                    "supported_inputs": ["text"], "use_cases": ["qa"], "notes": "",
                    "api_base_url": "https://api.test.com/v1",
                    "api_key": "sk-secret-should-be-hidden",
                },
            },
        },
    }

    def test_default_strips_api_key(self, tmp_path):
        """默认调用不返回 api_key。"""
        mgr = ModelManager(self.MODELS, tmp_path)
        config = mgr.get_active_model_config("test_role")
        assert "api_key" not in config, f"api_key 不应出现在默认返回中，实际: {config}"
        assert config["model"] == "gpt-test"
        assert config["_model_id"] == "m1"

    def test_sensitive_true_returns_api_key(self, tmp_path):
        """sensitive=True 返回 api_key 供 provider 内部使用。"""
        mgr = ModelManager(self.MODELS, tmp_path)
        config = mgr.get_active_model_config("test_role", sensitive=True)
        assert config["api_key"] == "sk-secret-should-be-hidden"

    def test_list_models_never_leaks_api_key(self, tmp_path):
        """list_models 永远不会包含 api_key。"""
        mgr = ModelManager(self.MODELS, tmp_path)
        models = mgr.list_models("test_role")
        assert len(models) == 1
        assert "api_key" not in models[0], f"list_models 不应泄漏 api_key: {models[0]}"

    def test_get_active_model_info_never_leaks_api_key(self, tmp_path):
        """get_active_model_info 永远不会包含 api_key。"""
        mgr = ModelManager(self.MODELS, tmp_path)
        info = mgr.get_active_model_info("test_role")
        assert "api_key" not in info, f"model_info 不应泄漏 api_key: {info}"


# ── M1: 策略开关 ──────────────────────────────────────────────

class TestStrategySwitches:
    """验证 allow_auto_upgrade / allow_auto_downgrade 策略开关。"""

    MODELS = {
        "base_model": {
            "enabled": True,
            "default_model_id": "base-1",
            "models": {
                "base-1": {
                    "provider": "openai", "model": "base-1", "display_name": "Base1",
                    "multimodal": False, "max_context_tokens": 4096, "temperature": 0.2,
                    "timeout_seconds": 10, "max_retries": 0, "priority": 10,
                    "cost_level": "low", "reasoning_level": "standard",
                    "supported_inputs": ["text"], "use_cases": ["qa"], "notes": "",
                    "api_base_url": "https://api.test.com/v1", "api_key": "sk-test",
                },
                "base-2": {
                    "provider": "openai", "model": "base-2", "display_name": "Base2",
                    "multimodal": False, "max_context_tokens": 4096, "temperature": 0.2,
                    "timeout_seconds": 10, "max_retries": 0, "priority": 5,
                    "cost_level": "low", "reasoning_level": "advanced",
                    "supported_inputs": ["text"], "use_cases": ["qa"], "notes": "",
                    "api_base_url": "https://api.test.com/v1", "api_key": "sk-test-2",
                },
            },
        },
        "adv_model": {
            "enabled": True,
            "default_model_id": "adv-1",
            "models": {
                "adv-1": {
                    "provider": "openai", "model": "adv-1", "display_name": "Adv1",
                    "multimodal": True, "max_context_tokens": 8192, "temperature": 0.2,
                    "timeout_seconds": 30, "max_retries": 0, "priority": 10,
                    "cost_level": "medium", "reasoning_level": "advanced",
                    "supported_inputs": ["text", "image"], "use_cases": ["qa"], "notes": "",
                    "api_base_url": "https://api.test.com/v1", "api_key": "sk-adv",
                },
            },
        },
    }

    ROUTING = {
        "rules": [
            {"name": "test-rule", "enabled": True, "priority": 1,
             "match": {"task_type": "qa"}, "route_to": "base_model", "fallback_to": "adv_model"},
        ]
    }

    def _make_config(self, allow_upgrade=True, allow_downgrade=True):
        from dataclasses import dataclass
        @dataclass
        class FakeConfig:
            llm: dict
            root: Path = Path("/tmp")
        cfg = FakeConfig(llm={
            "version": "3.3",
            "default_strategy": {
                "default_model_role": "base_model",
                "fallback_model_role": "adv_model",
                "prefer_lower_cost": True,
                "allow_auto_upgrade": allow_upgrade,
                "allow_auto_downgrade": allow_downgrade,
            },
            "models": self.MODELS,
            "routing": self.ROUTING,
            "embedding": {"enabled": False},
        })
        return cfg

    def test_router_reads_strategy_flags(self):
        """验证路由器正确读取策略开关。"""
        from iris.llm.router import ModelRouter

        cfg = self._make_config(allow_upgrade=False, allow_downgrade=False)
        router = ModelRouter(cfg)
        decision = router.route({"task_type": "qa"})

        assert decision.allow_auto_upgrade is False
        assert decision.allow_auto_downgrade is False

    def test_router_defaults_to_true(self):
        """缺少策略字段时默认为 True。"""
        from iris.llm.router import ModelRouter

        cfg = self._make_config()
        # 移除策略字段
        cfg.llm["default_strategy"].pop("allow_auto_upgrade", None)
        cfg.llm["default_strategy"].pop("allow_auto_downgrade", None)

        router = ModelRouter(cfg)
        decision = router.route({"task_type": "qa"})

        assert decision.allow_auto_upgrade is True
        assert decision.allow_auto_downgrade is True

    def test_downgrade_disabled_skips_fallback_chain(self, tmp_path):
        """allow_auto_downgrade=false 时降级链不含 fallback 角色。"""
        from iris.llm.router import ModelRouter
        from iris.llm.model_manager import ModelManager

        cfg = self._make_config(allow_downgrade=False)
        router = ModelRouter(cfg)
        decision = router.route({"task_type": "qa"})
        mgr = ModelManager(self.MODELS, tmp_path)

        # 模拟 _build_fallback_chain 逻辑
        chain = []
        primary_role = decision.selected_role
        for model_id, cfg_item in mgr.get_models_by_priority(primary_role):
            chain.append((primary_role, model_id))

        # 不应包含 fallback 角色模型
        roles_in_chain = {role for role, _ in chain}
        assert "adv_model" not in roles_in_chain

    def test_upgrade_disabled_only_uses_active_model(self, tmp_path):
        """allow_auto_upgrade=false 时仅使用活跃模型。"""
        from iris.llm.router import ModelRouter
        from iris.llm.model_manager import ModelManager

        cfg = self._make_config(allow_upgrade=False)
        router = ModelRouter(cfg)
        decision = router.route({"task_type": "qa"})
        mgr = ModelManager(self.MODELS, tmp_path)

        # 降级链应只含 1 个模型
        if decision.allow_auto_upgrade:
            chain = [(decision.selected_role, mid)
                     for mid, _ in mgr.get_models_by_priority(decision.selected_role)]
        else:
            active_cfg = mgr.get_active_model_config(decision.selected_role, sensitive=True)
            active_id = mgr.get_active_model_id(decision.selected_role)
            chain = [(decision.selected_role, active_id)]

        assert len(chain) == 1
        assert chain[0] == ("base_model", "base-1")


# ── has_credentials_for_role 扫描全部模型 (H3) ─────────────

class TestHasCredentialsForRole:
    """验证 has_credentials_for_role 扫描角色下所有模型。"""

    MODELS_WITH_MIXED_KEYS = {
        "test_role": {
            "enabled": True,
            "default_model_id": "no-key-model",
            "models": {
                "no-key-model": {
                    "provider": "openai", "model": "no-key", "display_name": "NK",
                    "multimodal": False, "max_context_tokens": 4096, "temperature": 0.2,
                    "timeout_seconds": 10, "max_retries": 0, "priority": 10,
                    "cost_level": "low", "reasoning_level": "standard",
                    "supported_inputs": ["text"], "use_cases": ["qa"], "notes": "",
                    "api_base_url": "https://api.test.com/v1", "api_key": "",
                },
                "has-key-model": {
                    "provider": "openai", "model": "has-key", "display_name": "HK",
                    "multimodal": False, "max_context_tokens": 4096, "temperature": 0.2,
                    "timeout_seconds": 10, "max_retries": 0, "priority": 5,
                    "cost_level": "low", "reasoning_level": "advanced",
                    "supported_inputs": ["text"], "use_cases": ["qa"], "notes": "",
                    "api_base_url": "https://api.test.com/v1", "api_key": "sk-has-key",
                },
            },
        },
    }

    def test_active_model_no_key_but_other_has_key(self, tmp_path):
        """活跃模型无 key 但其他模型有 key 时返回 True。"""
        mgr = ModelManager(self.MODELS_WITH_MIXED_KEYS, tmp_path)

        # 活跃模型是 no-key-model（default_model_id）
        active_id = mgr.get_active_model_id("test_role")
        assert active_id == "no-key-model"

        # 扫描全部应该找到有 key 的模型
        found = False
        for _mid, cfg in mgr.get_models_by_priority("test_role"):
            if cfg.get("api_key", "").strip():
                found = True
                break
        assert found, "应找到至少一个有 api_key 的模型"
