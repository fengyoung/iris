"""CLI 辅助函数 — 输出格式化、配置展示、Wiki 自动发现等共享工具。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from iris.app.banner import BANNER
from iris.llm import LLMService
from iris.output.formatter import format_payload
from iris.utils.logging import IrisLogger


# ── Banner ────────────────────────────────────────────────

_BANNER_COMMANDS = frozenset({
    "status", "diagnose", "check-config",
})


def _show_banner(command: str) -> None:
    if command not in _BANNER_COMMANDS:
        return
    print(BANNER, file=sys.stderr)


# ── 输出 ─────────────────────────────────────────────────


def _emit_output(command: str, payload: Any, *, pretty: bool) -> None:
    if pretty:
        rendered = format_payload(command, payload)
        if rendered:
            print(rendered)
            return
    _emit_json(payload)


def _emit_json(payload: Dict[str, Any]) -> None:
    try:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except BrokenPipeError:
        pass


# ── Payload 辅助 ──────────────────────────────────────────


def _scan_payload(summary, summary_only: bool) -> Dict[str, Any]:
    payload = summary.to_dict()
    if summary_only:
        payload.pop("documents", None)
    return payload


def _chunk_payload(summary, summary_only: bool) -> Dict[str, Any]:
    payload = summary.to_dict()
    if summary_only:
        payload.pop("chunks", None)
    return payload


# ── 配置摘要 ─────────────────────────────────────────────


def _print_config_summary(bundle) -> None:
    data_source = bundle.data_source
    default_source_name = data_source["default_source"]
    default_source = data_source["sources"][default_source_name]
    default_model_role = bundle.llm["default_strategy"]["default_model_role"]

    print("Iris 配置检查通过")
    print(f"- 项目根目录: {bundle.root}")
    print(f"- 默认数据源: {default_source_name}")
    print(f"- 数据源路径: {default_source['path']}")
    print(f"- 数据源只读: {default_source['read_only']}")
    print("- 全部数据源:")
    for name, cfg in data_source["sources"].items():
        enabled = cfg.get("enabled", True)
        status = "启用" if enabled else "禁用"
        print(f"    - {name} ({status}): {cfg['path']}")
    print(f"- 默认模型角色: {default_model_role}")

    try:
        llm_service = LLMService(bundle)
        mm = llm_service.get_provider().get_model_manager()
        base_info = mm.get_active_model_info("base_model")
        adv_info = mm.get_active_model_info("adv_model")
        print(f"- 基础模型: [{base_info['model_id']}] {base_info['provider']} {base_info['model']}")
        print(f"- 增强模型: [{adv_info['model_id']}] {adv_info['provider']} {adv_info['model']}")
    except (KeyError, ValueError, AttributeError):
        pass


# ── 诊断 / 状态（去掉 Wiki 相关统计） ─────────────────────


def _build_diagnose_payload(bundle, logger: IrisLogger) -> Dict[str, Any]:
    data_source = bundle.data_source
    default_source_name = data_source["default_source"]
    default_source = data_source["sources"].get(default_source_name, {})
    llm_service = LLMService(bundle)
    provider = llm_service.get_provider()
    route = provider.resolve({"input_type": "text", "task_type": "qa", "complexity": "standard", "use_case": "qa"})

    base_model_info = provider.get_model_manager().get_active_model_info("base_model")
    adv_model_info = provider.get_model_manager().get_active_model_info("adv_model")

    return {
        "project_root": str(bundle.root),
        "data_source_exists": Path(default_source.get("path", "")).exists() if default_source else False,
        "data_source_read_only": default_source.get("read_only", True) if default_source else True,
        "default_route_role": route.selected_role,
        "default_route_rule": route.matched_rule,
        "base_model_has_key": provider.has_credentials_for_role("base_model"),
        "adv_model_has_key": provider.has_credentials_for_role("adv_model"),
        "base_active_model_id": base_model_info["model_id"],
        "base_active_model_name": base_model_info["model"],
        "base_active_provider": base_model_info["provider"],
        "adv_active_model_id": adv_model_info["model_id"],
        "adv_active_model_name": adv_model_info["model"],
        "adv_active_provider": adv_model_info["provider"],
        "log_file": str(logger.log_path),
    }


def _credential_looks_usable(api_key: str) -> bool:
    """判断解包后的凭证是否像真凭证——空串或掩码（全星号）都算不可用。

    掩码形态来自 Pydantic SecretStr 被 f-string 插值，是「配置看着有值、
    请求必然 401」的典型伪装，健康检查必须把它和真凭证区分开。
    """
    key = (api_key or "").strip()
    return bool(key) and set(key) != {"*"}


def _build_vector_channel_health(bundle) -> Dict[str, Any]:
    """向量检索通道健康——纯静态检查，不发真实 embedding 请求。

    覆盖两类此前只能靠「检索质量悄悄变差」才察觉的故障：
      ① 凭证不可用（掩码/空）——请求必然 401，又被降级兜底吞掉；
      ② 索引缺失，或 embedder 模型与索引不一致——向量通道被静默禁用。

    embedder 刻意走与检索同一条构造路径（enhanced._init_embedder），
    这样「status 报健康」才等价于「检索真的能用」，而不是只复述配置文件。
    """
    from iris.retrieval.enhanced import _init_embedder
    from iris.retrieval.vector_index import VectorIndex

    emb_cfg = (bundle.llm.get("embedding", {}) or {}) if bundle.llm else {}
    if not emb_cfg.get("enabled", False):
        return {"enabled": False, "status": "disabled"}

    expected_model = emb_cfg.get("model", "text-embedding-v3")
    try:
        embedder = _init_embedder(bundle)
    except (OSError, ValueError, AttributeError, KeyError):
        embedder = None

    health: Dict[str, Any] = {
        "enabled": True,
        "expected_model": expected_model,
        "embedder_ready": embedder is not None,
        "credential_usable": _credential_looks_usable(
            getattr(embedder, "_api_key", "") if embedder else ""),
    }

    metadata_root = bundle.root / "data" / "metadata"
    indexes: Dict[str, Any] = {}
    for source_name, cfg in (bundle.data_source.get("sources", {}) or {}).items():
        if not cfg.get("enabled", True):
            continue
        meta = VectorIndex(metadata_root / f"{source_name}_vector_index").read_meta()
        stored_model = meta.get("embedder_model", "")
        indexes[source_name] = {
            "chunk_count": meta.get("count", 0),
            "stored_model": stored_model or None,
            "model_ok": stored_model == expected_model,
        }
    health["indexes"] = indexes

    if not health["embedder_ready"] or not health["credential_usable"]:
        health["status"] = "degraded"          # 必然降级为纯词法检索
    elif not indexes or any(not i["model_ok"] for i in indexes.values()):
        health["status"] = "degraded"          # 索引缺失或模型不匹配
    else:
        health["status"] = "ok"
    return health


def _build_status_payload(bundle, logger: IrisLogger) -> Dict[str, Any]:
    payload = _build_diagnose_payload(bundle, logger)
    freshness = _compute_freshness(bundle)
    # 补充 Wiki 状态（若已配置）
    wiki_info = {}
    if bundle.wiki:
        wiki_root = Path(bundle.wiki["wiki_root"])
        if wiki_root.exists():
            wiki_info["wiki_page_count"] = len(
                [p for p in wiki_root.rglob("*.md") if ".bak." not in p.stem])
    payload.update({
        "latest_source_mtime": freshness["latest_source_mtime"],
        "suggested_next_action": freshness["suggested_next_action"],
        "vector_channel": _build_vector_channel_health(bundle),
        **wiki_info,
    })
    return payload


def _auto_discover_wiki(bundle, *, changed_count: int = 0) -> Dict[str, Any]:
    import datetime as dt
    import json

    state_path = bundle.root / "data" / "wiki_discover_state.json"
    auto_candidates_path = bundle.root / "data" / "wiki_candidates_auto.jsonl"

    if changed_count == 0:
        return {"triggered": False, "reason": "无文档变更", "new_candidates": 0}

    try:
        from iris.wiki import CandidateDiscovery
        discovery = CandidateDiscovery(bundle)
        candidates = discovery.discover(limit=20, incremental=True)
        new_count = len([c for c in candidates if not c.has_wiki])
        if candidates:
            discovery.export_jsonl(candidates, auto_candidates_path)
        state = {"last_discover_at": dt.datetime.now().isoformat(),
                 "changed_source_count": changed_count, "new_candidates": new_count}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"triggered": True, "changed_documents": changed_count, "new_candidates": new_count,
                "candidates_file": str(auto_candidates_path) if candidates else None}
    except (OSError, json.JSONDecodeError, ValueError, AttributeError) as exc:
        return {"triggered": False, "reason": f"发现失败: {exc}", "new_candidates": 0}


def _run_sync_memory(bundle) -> Dict[str, Any]:
    project_root = bundle.root
    try:
        from iris.core.script_loader import load_script_module
        mod = load_script_module("sync_memory.py", project_root)
        sys_mem_dir = mod._system_memory_dir(project_root)
        iris_mem_dir = project_root / "memory"
        return mod.run_sync(sys_mem_dir, iris_mem_dir, dry_run=False, reverse=True)
    except (ImportError, AttributeError) as exc:
        return {"synced": False, "error": f"加载 sync_memory 失败: {exc}"}


def _build_agent_spec_payload(capabilities=None) -> Dict[str, Any]:
    """从 AgentCapability 列表构建 agent-spec 输出。

    capabilities 来自 iris.core.agent_adapter.IRIS_CAPABILITIES，
    是 agent 能力的唯一真相来源。
    """
    if capabilities is None:
        # 回退：尝试从 adapter 加载
        try:
            from iris.core.agent_adapter import IRIS_CAPABILITIES as caps
            capabilities = caps
        except ImportError:
            capabilities = []

    commands = {}
    for cap in capabilities:
        commands[cap.name] = {
            "purpose": cap.description,
            "command": cap.command,
            "tags": cap.tags,
            "inputs": list(cap.input_schema.keys()) if cap.input_schema else [],
        }
    from iris import __version__
    return {"protocol_version": __version__, "command_count": len(commands), "commands": commands}


# ── 参数解析辅助 ─────────────────────────────────────────


def _parse_context(raw: str) -> Dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("--context 必须是 JSON 对象")
    return data


def _parse_image_list(raw: str) -> List[str]:
    if not raw.strip():
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


# ── 索引陈旧度检查（去掉 Wiki 相关） ──────────────────────


def _write_text_file(raw_path: str, text: str) -> Path:
    from iris.utils.shared import atomic_write_text

    output_path = Path(raw_path)
    atomic_write_text(output_path, text)
    return output_path


def _write_bytes_file(raw_path: str, data: bytes) -> Path:
    from iris.utils.shared import atomic_write_bytes

    output_path = Path(raw_path)
    atomic_write_bytes(output_path, data)
    return output_path


def _resolve_output_path(output_file: str, query: str, ext: str) -> Path:
    if output_file:
        return Path(output_file)
    import re
    slug = re.sub(r'[^\w一-鿿-]', '_', query)[:50]
    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{slug}{ext}"


def _compute_freshness(bundle) -> Dict[str, Any]:
    metadata_root = bundle.root / "data" / "metadata"
    sources = bundle.data_source["sources"]
    latest_source_mtime = 0.0
    source_statuses = []

    for source_name, cfg in sources.items():
        if not cfg.get("enabled", True):
            continue
        scan_path = metadata_root / f"{source_name}_scan_summary.json"
        chunk_path = metadata_root / f"{source_name}_chunk_summary.json"
        source_mtime = 0.0

        if scan_path.exists():
            try:
                scan_payload = json.loads(scan_path.read_text(encoding="utf-8"))
                stored_mtime = scan_payload.get("latest_mtime", 0)
                if stored_mtime:
                    source_mtime = float(stored_mtime)
                else:
                    for doc in scan_payload.get("documents", []):
                        try:
                            mtime = datetime.fromisoformat(doc["modified_at"]).timestamp()
                            if mtime > source_mtime:
                                source_mtime = mtime
                        except (ValueError, KeyError):
                            continue
            except (json.JSONDecodeError, OSError, KeyError, ValueError):
                pass
            if source_mtime > latest_source_mtime:
                latest_source_mtime = source_mtime

        scan_mtime = scan_path.stat().st_mtime if scan_path.exists() else 0.0
        chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else 0.0
        scan_stale = bool(source_mtime and scan_mtime and source_mtime > scan_mtime + 1)
        index_stale = bool(source_mtime and chunk_mtime and source_mtime > chunk_mtime + 1)
        source_statuses.append({
            "source_name": source_name,
            "scan_exists": scan_path.exists(),
            "chunk_exists": chunk_path.exists(),
            "scan_stale": scan_stale,
            "index_stale": index_stale,
        })

    suggested = "系统就绪"
    for st in source_statuses:
        if not st["scan_exists"]:
            suggested = f"数据源 {st['source_name']} 缺少扫描（步骤 2 中启用）"
            break
        if not st["chunk_exists"]:
            suggested = f"数据源 {st['source_name']} 缺少索引（步骤 2 中启用）"
            break
        if st["index_stale"]:
            suggested = f"数据源 {st['source_name']} 有更新（步骤 2 中处理）"
            break

    return {
        "latest_source_mtime": latest_source_mtime,
        "source_statuses": source_statuses,
        "suggested_next_action": suggested,
    }
