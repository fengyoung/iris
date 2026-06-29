"""配置加载与校验模块。"""

from .loader import ConfigBundle, load_config_bundle
from .models import ConfigBundleV2  # Pydantic v2 类型安全配置（从 iris2 迁移）

__all__ = ["ConfigBundle", "load_config_bundle", "ConfigBundleV2"]
