"""模型注册表：管理多模型配置解析、切换与状态持久化。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# model_id 分隔符
# ---------------------------------------------------------------------------

ROLE_MODEL_SEP = "."


def encode_model_ref(role: str, model_id: str) -> str:
    """将角色和模型 ID 编码为唯一引用。"""
    return f"{role}{ROLE_MODEL_SEP}{model_id}"


def decode_model_ref(ref: str) -> tuple[str, str]:
    """从唯一引用解码出角色和模型 ID。"""
    parts = ref.split(ROLE_MODEL_SEP, 1)
    if len(parts) != 2:
        raise ValueError(f"无效的模型引用: {ref}")
    return parts[0], parts[1]


# ---------------------------------------------------------------------------
# ModelManagerError
# ---------------------------------------------------------------------------


class ModelManagerError(RuntimeError):
    """模型管理相关错误。"""


# ---------------------------------------------------------------------------
# ModelManager
# ---------------------------------------------------------------------------


class ModelManager:
    """模型注册表。

    管理每个角色下的多模型注册表，提供活跃模型解析、切换、列表功能。
    活跃模型状态持久化在 data/active_model.json 中。
    """

    def __init__(self, models: Dict[str, Any], state_dir: Path):
        self._models = models
        self._state_path = state_dir / "active_model.json"
        self._state = self._load_state()

    # -- public API ---------------------------------------------------------

    def get_active_model_id(self, role: str) -> str:
        """获取指定角色的当前活跃模型 ID。"""
        role_container = self._models.get(role)
        if not role_container:
            raise ModelManagerError(f"未知角色: {role}")

        default_id = role_container.get("default_model_id", "")
        if not default_id:
            raise ModelManagerError(f"角色 {role} 未配置 default_model_id")

        model_id = self._state.get(role, default_id)
        if model_id not in role_container.get("models", {}):
            model_id = default_id

        return model_id

    def get_active_model_config(self, role: str, *, sensitive: bool = False) -> Dict[str, Any]:
        """获取指定角色的当前活跃模型完整配置。

        Args:
            role: 模型角色名
            sensitive: 若为 True，返回包含 api_key 的完整配置（仅供 provider 内部使用）
        """
        model_id = self.get_active_model_id(role)
        original = self._models[role]["models"][model_id]
        # 返回浅拷贝并注入 _model_id，避免修改内部状态
        config = dict(original, _model_id=model_id)
        # 从 Pydantic SecretStr 中提取原始值（dict() 迭代返回 SecretStr 实例）
        from pydantic import SecretStr as _SecretStr
        if "api_key" in config and isinstance(config["api_key"], _SecretStr):
            config["api_key"] = config["api_key"].get_secret_value()
        if not sensitive:
            config.pop("api_key", None)
        return config

    def switch_model(self, role: str, model_id: str) -> bool:
        """切换指定角色的活跃模型。成功返回 True。"""
        role_container = self._models.get(role)
        if not role_container:
            return False

        if model_id not in role_container.get("models", {}):
            return False

        self._state[role] = model_id
        self._save_state()
        return True

    def list_models(self, role: str) -> List[Dict[str, Any]]:
        """列出指定角色下的所有可用模型（含元信息）。"""
        role_container = self._models.get(role)
        if not role_container:
            return []

        active_id = self.get_active_model_id(role)
        result: List[Dict[str, Any]] = []
        for mid, cfg in role_container.get("models", {}).items():
            result.append({
                "model_id": mid,
                "provider": cfg.get("provider", ""),
                "model": cfg.get("model", ""),
                "display_name": cfg.get("display_name", mid),
                "multimodal": cfg.get("multimodal", False),
                "cost_level": cfg.get("cost_level", ""),
                "is_active": mid == active_id,
            })
        return result

    def get_models_by_priority(self, role: str) -> List[tuple]:
        """获取指定角色下所有模型，按 priority 降序排列。

        Returns:
            [(model_id, model_config), ...] 列表，priority 高的在前
        """
        role_container = self._models.get(role)
        if not role_container:
            return []

        models = role_container.get("models", {})
        sorted_models = sorted(
            models.items(),
            key=lambda item: item[1].get("priority", 0),
            reverse=True,
        )
        return sorted_models

    def get_active_model_info(self, role: str) -> Dict[str, Any]:
        """获取指定角色当前活跃模型的摘要信息。

        source 字段说明活跃模型来源：
          - "override"：来自 data/active_model.json 的显式切换
          - "default"：来自 llm.json 的 default_model_id
        """
        model_id = self.get_active_model_id(role)
        config = self.get_active_model_config(role, sensitive=True)
        default_id = self._models.get(role, {}).get("default_model_id", "")
        source = "override" if self._state.get(role) and self._state.get(role) != default_id else "default"
        return {
            "model_id": model_id,
            "provider": config.get("provider", ""),
            "model": config.get("model", ""),
            "display_name": config.get("display_name", model_id),
            "multimodal": config.get("multimodal", False),
            "cost_level": config.get("cost_level", ""),
            "api_base_url": config.get("api_base_url", ""),
            "source": source,
        }

    def find_model_by_name(self, model_name: str) -> Optional[Dict[str, Any]]:
        """在所有角色中按 model 字段或 model_id 查找模型配置（含 api_key）。

        兼容两种配置形态：raw dict（测试/直载）与 Pydantic 配置模型
        （BaseConfigModel 提供 dict 风格 get 访问，dict() 构造时
        __getitem__ 会自动解包 SecretStr）。

        Returns:
            包含 api_key 的完整配置（附 _model_id 字段），未找到返回 None。
        """
        for role, role_container in self._models.items():
            models = role_container.get("models", {}) if hasattr(role_container, "get") else {}
            for model_id, cfg in models.items():
                cfg_model = cfg.get("model") if hasattr(cfg, "get") else None
                if cfg_model == model_name or model_id == model_name:
                    result = dict(cfg, _model_id=model_id)
                    # 与 get_active_model_config 同款处理：dict() 迭代
                    # Pydantic 模型时 api_key 为 SecretStr，需显式解包
                    from pydantic import SecretStr as _SecretStr
                    if "api_key" in result and isinstance(result["api_key"], _SecretStr):
                        result["api_key"] = result["api_key"].get_secret_value()
                    return result
        return None

    # -- 内部方法 -----------------------------------------------------------

    def _load_state(self) -> Dict[str, str]:
        """从文件加载活跃模型状态。"""
        if not self._state_path.exists():
            return self._build_default_state()

        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return self._build_default_state()
            return data
        except (json.JSONDecodeError, OSError):
            logger.exception("读取 active_model.json 失败")
            return self._build_default_state()

    def _save_state(self) -> None:
        """保存活跃模型状态到文件。"""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_default_state(self) -> Dict[str, str]:
        """根据配置的 default_model_id 构建初始状态。"""
        state: Dict[str, str] = {}
        for role, container in self._models.items():
            if isinstance(container, dict) and "default_model_id" in container:
                state[role] = container["default_model_id"]
        return state
