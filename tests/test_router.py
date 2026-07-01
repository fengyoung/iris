"""模型路由模块测试。"""

from __future__ import annotations

from iris.config.models import RoutingRule
from iris.llm.router import ModelRouter


class TestModelRouter:
    def test_route_to_default(self, config_bundle):
        router = ModelRouter(config_bundle)
        decision = router.route({"unknown": "value"})
        assert decision.selected_role == "base_model"
        assert decision.matched_rule == "__default__"

    def test_route_by_task_type(self, config_bundle):
        router = ModelRouter(config_bundle)
        decision = router.route({"task_type": "qa"})
        assert decision.selected_role == "base_model"
        assert decision.matched_rule == "qa-rule"

    def test_route_complex(self, config_bundle):
        router = ModelRouter(config_bundle)
        decision = router.route({"complexity": "complex"})
        assert decision.selected_role == "adv_model"
        assert decision.matched_rule == "complex-rule"

    def test_fallback_role(self, config_bundle):
        router = ModelRouter(config_bundle)
        decision = router.route({"task_type": "qa"})
        assert decision.fallback_role == "adv_model"

    def test_disabled_rule_skipped(self, config_bundle):
        """禁用的规则应该被跳过。"""
        rules = config_bundle.llm["routing"]["rules"]
        # Pydantic model_copy 替换第一条规则的 enabled 为 False
        rules[0] = rules[0].model_copy(update={"enabled": False})
        router = ModelRouter(config_bundle)
        decision = router.route({"task_type": "qa"})
        assert decision.matched_rule == "__default__"

    def test_rule_priority(self, config_bundle):
        """低 priority 值（高优先级）优先匹配。"""
        rules = config_bundle.llm["routing"]["rules"]
        # 插入一条 priority=0 的规则（高于原有的 priority=1）
        rules.insert(0, RoutingRule(
            name="high-priority", enabled=True, priority=0,
            match={"task_type": "qa"}, route_to="adv_model",
        ))
        router = ModelRouter(config_bundle)
        decision = router.route({"task_type": "qa"})
        # 高优先级（priority=0）匹配
        assert decision.selected_role == "adv_model"
        assert decision.matched_rule == "high-priority"
