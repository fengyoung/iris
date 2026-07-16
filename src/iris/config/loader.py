"""加载和校验 Iris JSON 配置，支持 .env 变量注入与路径占位符。

v3.19: ConfigBundle 统一为 ConfigBundleV2 (Pydantic v2)，旧 ConfigBundle dataclass 已删除。
       load_config_bundle() 返回类型为 ConfigBundleV2，同时向后兼容 dict 访问。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from iris.config.models import ConfigBundleV2

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """配置文件不合法时抛出。"""


def ConfigBundle(
    root: Path,
    app: Dict[str, Any],
    data_source: Dict[str, Any],
    llm: Dict[str, Any],
    wiki: Dict[str, Any] | None = None,
    meeting_routes: Dict[str, Any] | None = None,
    feishu_ingest: Dict[str, Any] | None = None,
) -> ConfigBundleV2:
    """向后兼容的配置构造函数。

    v3.19: 旧 ConfigBundle dataclass 已删除，此工厂函数保持与旧签名的兼容。
    内部委托给 ConfigBundleV2.from_dicts()，缺失字段自动填充默认值。
    生产代码请使用 load_config_bundle() 获取完整校验的配置。
    """
    return ConfigBundleV2.from_dicts(
        root=root,
        app_dict=app,
        data_source_dict=data_source,
        llm_dict=llm,
        wiki_dict=wiki or {},
        meeting_routes=meeting_routes,
        feishu_ingest_dict=feishu_ingest or {},
    )


# ---------------------------------------------------------------------------
# 环境变量解析
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def load_env_file(env_path: Optional[Path] = None) -> Dict[str, str]:
    """从 .env 文件加载键值对，格式 key=value，支持 # 注释和引号包裹的值。"""
    env: Dict[str, str] = {}
    if env_path is None:
        return env
    if not env_path.exists():
        return env

    with env_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 去除可选引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key:
                env[key] = value
    return env


def resolve_env_vars(
    data: Any,
    env: Dict[str, str],
    seen: Optional[set] = None,
) -> Any:
    """替换字符串中的 ${VAR} 占位符（单层，不递归展开嵌套变量）。

    查找优先级：OS 环境变量 > .env 文件变量 > macOS Keychain。
    未找到的占位符保留原样（不抛出异常）。

    注意：仅做单层替换 —— 如果 ${A} 的值是 "${B}"，则 "${B}" 会保留
    在结果中不会被二次展开。这是有意为之，避免不可预期的递归行为。
    """
    if isinstance(data, str):
        if _ENV_PATTERN.search(data):
            def _replace(m: re.Match) -> str:
                var_name = m.group(1)
                # OS 环境变量优先
                val = os.environ.get(var_name)
                if val is not None:
                    return val
                # 其次 .env 文件变量
                val = env.get(var_name)
                if val is not None:
                    return val
                # 最后尝试 macOS Keychain
                try:
                    from iris.config.secrets import get_secret
                    val = get_secret(var_name)
                    if val is not None:
                        return val
                except (ImportError, OSError) as exc:
                    logger.debug("Keychain 查找 %s 失败: %s", var_name, exc)
                # 未找到，保留占位符
                return m.group(0)
            return _ENV_PATTERN.sub(_replace, data)
        return data
    elif isinstance(data, dict):
        return {k: resolve_env_vars(v, env, seen) for k, v in data.items()}
    elif isinstance(data, list):
        return [resolve_env_vars(item, env, seen) for item in data]
    return data


def resolve_path_vars(data: Any, project_root: Path) -> Any:
    """递归替换字符串中的 ${IRIS_XXX_DIR} 路径占位符。

    已知占位符：
      - ${IRIS_PROJECT_ROOT} → project_root
      - ${IRIS_DATA_DIR}     -> {project_root}/data
      - ${IRIS_OUTPUT_DIR}   -> {project_root}/output
      - ${IRIS_MEMORY_DIR}   -> {project_root}/memory
      - ${IRIS_WORK_DOCS_DIR} -> 用户配置（由 .env 或 OS 环境提供）
    """
    _PATH_MAP = {
        "${IRIS_PROJECT_ROOT}": str(project_root),
        "${IRIS_DATA_DIR}": str(project_root / "data"),
        "${IRIS_OUTPUT_DIR}": str(project_root / "output"),
        "${IRIS_MEMORY_DIR}": str(project_root / "memory"),
        # ${IRIS_WORK_DOCS_DIR} 和 ${IRIS_WIKI_ROOT} 由 .env 解析，
        # 这里不会重复替换（如果 env 层已解析则已变为真实路径）
    }

    if isinstance(data, str):
        for placeholder, resolved in _PATH_MAP.items():
            if placeholder in data:
                data = data.replace(placeholder, resolved)
        return data
    elif isinstance(data, dict):
        return {k: resolve_path_vars(v, project_root) for k, v in data.items()}
    elif isinstance(data, list):
        return [resolve_path_vars(item, project_root) for item in data]
    return data


# ---------------------------------------------------------------------------
# 配置文件加载
# ---------------------------------------------------------------------------

REQUIRED_CONFIG_FILES = {
    "app": "app.json",
    "data_source": "data_source.json",
    "llm": "llm.json",
}


def load_config_bundle(
    project_root: Path | str,
    *,
    env_file: Optional[Path] = None,
) -> ConfigBundleV2:
    """从项目根目录加载全部配置，支持 .env 变量注入。

    返回 ConfigBundleV2（Pydantic v2），类型安全且向后兼容 dict 访问。

    加载流程：
      1. 加载 .env 文件（若存在）
      2. 按 REQUIRED_CONFIG_FILES 依次加载配置文件
         优先加载 config/*.json，若不存在则回退到 config/*.json.example
      3. 解析所有 ${VAR} 占位符（OS 环境变量 > .env 变量）
      4. 解析项目路径占位符（${IRIS_XXX_DIR} → 绝对路径）
      5. 校验配置
    """

    root = Path(project_root).resolve()
    config_root = root / "config"

    # 加载 .env
    if env_file is None:
        env_file = root / ".env"
    env = load_env_file(env_file)
    _check_plaintext_keys(env_file)

    # 配置文件加载，支持 .example 回退
    loaded: Dict[str, Any] = {}
    for name, filename in REQUIRED_CONFIG_FILES.items():
        config_path = config_root / filename
        if not config_path.exists():
            example_path = config_root / f"{filename}.example"
            if example_path.exists():
                config_path = example_path
            else:
                raise ConfigError(
                    f"缺少配置文件: {filename}，请从 {filename}.example 复制并配置"
                )
        loaded[name] = _load_json(config_path)

    # 环境变量解析
    for name in loaded:
        loaded[name] = resolve_env_vars(loaded[name], env)

    # 项目路径占位符解析
    for name in loaded:
        loaded[name] = resolve_path_vars(loaded[name], root)

    # ── 可选配置 ──────────────────────────────────────────────
    wiki_config = _load_optional_config(config_root, "wiki.json", env, root)
    meeting_routes_config = _load_optional_config(config_root, "meeting_routes.json", env, root)
    feishu_ingest_config = _load_optional_config(config_root, "feishu_ingest.json", env, root)

    # ── 通过 Pydantic v2 构建类型安全配置（自动校验） ─────────
    try:
        from pydantic import ValidationError
    except ImportError:
        ValidationError = None

    try:
        return ConfigBundleV2.from_dicts(
            root=root,
            app_dict=loaded["app"],
            data_source_dict=loaded["data_source"],
            llm_dict=loaded["llm"],
            wiki_dict=wiki_config,
            meeting_routes=meeting_routes_config,
            feishu_ingest_dict=feishu_ingest_config,
        )
    except Exception as exc:
        if ValidationError and isinstance(exc, ValidationError):
            # 格式化 Pydantic 字段级错误为可读形式
            errors = "; ".join(
                f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            )
            raise ConfigError(f"配置校验失败: {errors}") from exc
        raise ConfigError(f"配置加载异常: {exc}") from exc


_plaintext_keys_warned = False
_plaintext_keys_lock = threading.Lock()


def _check_plaintext_keys(env_path: Optional[Path]) -> None:
    """检测 .env 中是否包含明文 API Key，输出安全提醒（同进程仅一次）。"""
    global _plaintext_keys_warned
    if _plaintext_keys_warned:
        return
    if not env_path or not env_path.exists():
        return
    content = env_path.read_text(encoding="utf-8")
    key_count = 0
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped and any(kw in stripped.upper() for kw in ("API_KEY", "APIKEY", "TOKEN", "SECRET")):
            # 跳过空值占位（如 LARK_APP_SECRET=）
            val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            if not val:
                continue
            key_count += 1
    if key_count:
        with _plaintext_keys_lock:
            if _plaintext_keys_warned:
                return
            _plaintext_keys_warned = True
        msg = (
            f"⚠️  安全提醒：.env 文件中检测到 {key_count} 个明文 API Key/Token。"
            "建议迁移到 macOS Keychain（`iris secrets-set --key <NAME>`），"
            "然后从 .env 中移除对应明文值。"
        )
        logger.warning(msg)
        import sys
        print(msg, file=sys.stderr)


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"缺少配置文件: {path}")

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON 解析失败: {path} -> {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是对象: {path}")
    return data


def _load_optional_config(
    config_root: Path, filename: str, env: Dict[str, str], root: Path
) -> Optional[Dict[str, Any]]:
    """加载可选配置文件，优先实际文件，其次 .example 占位符。

    不存在任何文件时返回 None。
    """
    actual = config_root / filename
    if actual.exists():
        config = resolve_env_vars(_load_json(actual), env)
        return resolve_path_vars(config, root)

    example = config_root / f"{filename}.example"
    if example.exists():
        logger.warning("%s 不存在，回退加载 %s.example（占位符可能未解析）", filename, filename)
        config = resolve_env_vars(_load_json(example), env)
        return resolve_path_vars(config, root)

    return None


# ── 以下手工校验已由 ConfigBundleV2 的 Pydantic v2 校验替代 ──
# 包括：字段存在性、数据类型、数值范围、启用数据源数、role/model 一致性等
# 相关 Pydantic 模型：AppConfig / DataSourceConfig / LLMConfig / WikiConfig
