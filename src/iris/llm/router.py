"""模型路由最小实现。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from iris.config.loader import ConfigBundle, ConfigError


@dataclass(frozen=True)
class RoutingDecision:
    """模型路由结果。"""

    selected_role: str
    fallback_role: Optional[str]
    matched_rule: str


class ModelRouter:
    """基于配置规则做简单模型路由。"""

    def __init__(self, config: ConfigBundle):
        self._llm = config.llm
        self._models = self._llm["models"]
        self._rules = sorted(self._llm["routing"]["rules"], key=lambda item: item["priority"])
        self._default_role = self._llm["default_strategy"]["default_model_role"]
        self._fallback_role = self._llm["default_strategy"]["fallback_model_role"]

    def route(self, context: Dict[str, Any]) -> RoutingDecision:
        for rule in self._rules:
            if not rule.get("enabled", True):
                continue
            if _match_rule(rule["match"], context):
                selected_role = rule["route_to"]
                fallback_role = rule.get("fallback_to")
                self._ensure_role_enabled(selected_role, fallback_role)
                return RoutingDecision(
                    selected_role=selected_role,
                    fallback_role=fallback_role,
                    matched_rule=rule["name"],
                )

        self._ensure_role_enabled(self._default_role, self._fallback_role)
        return RoutingDecision(
            selected_role=self._default_role,
            fallback_role=self._fallback_role,
            matched_rule="__default__",
        )

    def _ensure_role_enabled(self, role: str, fallback_role: Optional[str]) -> None:
        if self._models[role].get("enabled", False):
            return
        if fallback_role and self._models[fallback_role].get("enabled", False):
            return
        raise ConfigError(f"模型不可用，且无可用回退角色: {role}")


def _match_rule(match: Dict[str, Any], context: Dict[str, Any]) -> bool:
    for key, expected in match.items():
        if context.get(key) != expected:
            return False
    return True
