"""加载和校验 Iris JSON 配置，支持 .env 变量注入与路径占位符。"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """配置文件不合法时抛出。"""


@dataclass(frozen=True)
class ConfigBundle:
    """聚合后的配置对象。"""

    root: Path
    app: Dict[str, Any]
    data_source: Dict[str, Any]
    llm: Dict[str, Any]
    wiki: Dict[str, Any] | None = None
    meeting_routes: Dict[str, Any] | None = None
    feishu_ingest: Dict[str, Any] | None = None


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
                except Exception:
                    pass
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
) -> ConfigBundle:
    """从项目根目录加载全部配置，支持 .env 变量注入。

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

    _validate_app_config(loaded["app"])
    _validate_data_source_config(loaded["data_source"])
    _validate_llm_config(loaded["llm"])

    # 可选加载 wiki.json（步骤 2 启用）
    wiki_config_path = config_root / "wiki.json"
    wiki_config = None
    if wiki_config_path.exists():
        wiki_config = resolve_env_vars(_load_json(wiki_config_path), env)
        wiki_config = resolve_path_vars(wiki_config, root)
    elif (config_root / "wiki.json.example").exists():
        logger.warning("wiki.json 不存在，回退加载 wiki.json.example（占位符可能未解析）")
        wiki_config = resolve_env_vars(_load_json(config_root / "wiki.json.example"), env)
        wiki_config = resolve_path_vars(wiki_config, root)

    # 可选加载 meeting_routes.json（纪要提取路由配置）
    meeting_routes_config_path = config_root / "meeting_routes.json"
    meeting_routes_config = None
    if meeting_routes_config_path.exists():
        meeting_routes_config = resolve_env_vars(_load_json(meeting_routes_config_path), env)
        meeting_routes_config = resolve_path_vars(meeting_routes_config, root)
    elif (config_root / "meeting_routes.json.example").exists():
        meeting_routes_config = resolve_env_vars(_load_json(config_root / "meeting_routes.json.example"), env)
        meeting_routes_config = resolve_path_vars(meeting_routes_config, root)

    # 可选加载 feishu_ingest.json（飞书文档/聊天提取配置）
    feishu_ingest_config_path = config_root / "feishu_ingest.json"
    feishu_ingest_config = None
    if feishu_ingest_config_path.exists():
        feishu_ingest_config = resolve_env_vars(_load_json(feishu_ingest_config_path), env)
        feishu_ingest_config = resolve_path_vars(feishu_ingest_config, root)
    elif (config_root / "feishu_ingest.json.example").exists():
        feishu_ingest_config = resolve_env_vars(_load_json(config_root / "feishu_ingest.json.example"), env)
        feishu_ingest_config = resolve_path_vars(feishu_ingest_config, root)

    return ConfigBundle(root=root, **loaded, wiki=wiki_config,
                        meeting_routes=meeting_routes_config,
                        feishu_ingest=feishu_ingest_config)


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


def _require_keys(data: Dict[str, Any], keys: Iterable[str], prefix: str) -> None:
    for key in keys:
        if key not in data:
            raise ConfigError(f"缺少必填字段: {prefix}{key}")


def _validate_app_config(config: Dict[str, Any]) -> None:
    _require_keys(config, ["version", "app", "paths", "session", "output", "qa", "logging", "safety"], "app.")
    if config["output"].get("default_output_mode") != "chat":
        raise ConfigError("app.output.default_output_mode 当前必须为 chat")
    qa = config["qa"]
    _require_keys(
        qa,
        [
            "max_prompt_context_chars",
            "max_evidence_blocks",
            "max_wiki_hits",
            "max_block_summary_chars",
            "max_wiki_summary_chars",
        ],
        "app.qa.",
    )
    for key in (
        "max_prompt_context_chars",
        "max_evidence_blocks",
        "max_wiki_hits",
        "max_block_summary_chars",
        "max_wiki_summary_chars",
    ):
        value = qa[key]
        if not isinstance(value, int) or value <= 0:
            raise ConfigError(f"app.qa.{key} 必须是正整数")


def _validate_data_source_config(config: Dict[str, Any]) -> None:
    _require_keys(config, ["version", "default_source", "sources", "ingestion"], "data_source.")
    sources = config["sources"]
    if not isinstance(sources, dict) or not sources:
        raise ConfigError("data_source.sources 至少需要一个数据源")

    default_source = config["default_source"]
    if default_source not in sources:
        raise ConfigError("data_source.default_source 必须存在于 sources 中")

    enabled_sources = 0
    for source_name, source in sources.items():
        _require_keys(
            source,
            ["enabled", "path", "format", "read_only", "include_patterns", "exclude_patterns"],
            f"data_source.sources.{source_name}.",
        )
        if source["enabled"]:
            enabled_sources += 1
        if source["format"] not in ("markdown", "pdf"):
            raise ConfigError(f"不支持的数据源格式: {source_name}.format={source['format']}，当前支持 markdown / pdf")

    if enabled_sources == 0:
        raise ConfigError("data_source.sources 至少需要启用一个数据源")

    default_config = sources[default_source]
    if not default_config.get("read_only", False):
        raise ConfigError("主数据源必须显式配置 read_only=true")


def _validate_llm_config(config: Dict[str, Any]) -> None:
    _require_keys(config, ["version", "default_strategy", "models", "routing"], "llm.")

    models = config["models"]
    for role in ("base_model", "adv_model"):
        if role not in models:
            raise ConfigError(f"llm.models 缺少角色: {role}")

        role_container = models[role]
        _require_keys(
            role_container,
            ["enabled", "default_model_id", "models"],
            f"llm.models.{role}.",
        )

        inner_models = role_container["models"]
        if not isinstance(inner_models, dict) or not inner_models:
            raise ConfigError(f"llm.models.{role}.models 必须是非空对象")

        default_id = role_container["default_model_id"]
        if default_id not in inner_models:
            raise ConfigError(
                f"llm.models.{role}.default_model_id '{default_id}' 未在 models 中定义"
            )

        advanced_role_has_text = False
        for mid, model_cfg in inner_models.items():
            _require_keys(
                model_cfg,
                ["provider", "model", "api_base_url", "api_key", "multimodal", "supported_inputs", "use_cases"],
                f"llm.models.{role}.models.{mid}.",
            )

            supported_inputs = model_cfg["supported_inputs"]
            if not isinstance(supported_inputs, list) or not supported_inputs:
                raise ConfigError(f"llm.models.{role}.models.{mid}.supported_inputs 必须是非空数组")
            if model_cfg["multimodal"] is False and "image" in supported_inputs:
                raise ConfigError(f"llm.models.{role}.models.{mid} 不支持多模态却声明了 image 输入")
            if role == "adv_model" and "text" in supported_inputs:
                advanced_role_has_text = True
            if not isinstance(model_cfg["api_base_url"], str) or not model_cfg["api_base_url"].strip():
                raise ConfigError(f"llm.models.{role}.models.{mid}.api_base_url 必须是非空字符串")
            if not isinstance(model_cfg["api_key"], str):
                raise ConfigError(f"llm.models.{role}.models.{mid}.api_key 必须是字符串")

        if role == "adv_model" and not advanced_role_has_text:
            raise ConfigError("llm.models.adv_model 至少要有一个模型支持 text 输入")

    strategy = config["default_strategy"]
    _require_keys(strategy, ["default_model_role", "fallback_model_role"], "llm.default_strategy.")
    for field in ("default_model_role", "fallback_model_role"):
        if strategy[field] not in models:
            raise ConfigError(f"llm.default_strategy.{field} 必须指向已定义模型")

    routing = config["routing"]
    rules = routing.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ConfigError("llm.routing.rules 必须是非空数组")

    seen_names = set()
    for rule in rules:
        _require_keys(rule, ["name", "enabled", "priority", "match", "route_to"], "llm.routing.rules[].")
        if rule["name"] in seen_names:
            raise ConfigError(f"llm.routing.rules 存在重复名称: {rule['name']}")
        seen_names.add(rule["name"])
        if rule["route_to"] not in models:
            raise ConfigError(f"llm.routing.rules.{rule['name']}.route_to 未定义")
        if "fallback_to" in rule and rule["fallback_to"] not in models:
            raise ConfigError(f"llm.routing.rules.{rule['name']}.fallback_to 未定义")
