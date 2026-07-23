"""pytest 共享 fixtures。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from iris.config.loader import ConfigBundle, load_config_bundle
from iris.config.secrets import KeychainError


# 顶层目录中实际为纯单元测试的文件（无 mock / I/O 依赖，应为 unit）
_TOPLEVEL_UNIT_FILES: set[str] = {
    "test_asr_hotwords.py",
    "test_biweekly_dedup.py",
    "test_biweekly_helpers.py",
    "test_embedder.py",
    "test_memory.py",
    "test_mindmap.py",
    "test_person_enricher.py",
    "test_qa_context.py",
    "test_qa_helpers.py",
    "test_router.py",
    "test_tokenization.py",
    "test_transcribe_meeting.py",
    "test_trello_models.py",
    "test_validation.py",
}


def pytest_collection_modifyitems(items):
    """按目录 + 白名单自动标记测试：tests/unit/ → unit，顶层纯逻辑文件 → unit，其余 → integration。"""
    for item in items:
        if not any(marker.name in ("unit", "integration") for marker in item.iter_markers()):
            test_path = str(item.fspath)
            filename = test_path.rsplit("/", 1)[-1] if "/" in test_path else test_path
            if "/unit/" in test_path or filename in _TOPLEVEL_UNIT_FILES:
                item.add_marker(pytest.mark.unit)
            else:
                item.add_marker(pytest.mark.integration)


@pytest.fixture
def temp_project() -> Path:
    """创建临时项目目录。"""
    tmp = Path(tempfile.mkdtemp())
    config_dir = tmp / "config"
    config_dir.mkdir()
    data_dir = tmp / "data"
    data_dir.mkdir()
    memory_dir = tmp / "memory"
    memory_dir.mkdir()
    return tmp


@pytest.fixture
def minimal_app_config() -> Dict[str, Any]:
    return {
        "version": "3.0",
        "app": {"name": "Iris", "env": "test", "language": "zh-CN", "timezone": "Asia/Shanghai"},
        "paths": {
            "project_root": "${IRIS_PROJECT_ROOT}",
            "output_dir": "${IRIS_OUTPUT_DIR}",
            "temp_dir": "./temp",
            "memory_dir": "${IRIS_MEMORY_DIR}",
            "template_dir": "./templates",
            "log_dir": "./logs",
        },
        "session": {
            "enable_session_memory": True,
            "enable_user_preferences": True,
            "working_context_file": "${IRIS_MEMORY_DIR}/working/working_context.md",
            "session_summary_dir": "${IRIS_MEMORY_DIR}/session",
            "auto_session_summary": True,
            "session_timeout_minutes": 30,
        },
        "output": {"default_output_mode": "chat", "include_citations_by_default": True, "answer_style": "balanced"},
        "qa": {
            "max_prompt_context_chars": 6000, "max_evidence_blocks": 6,
            "max_wiki_hits": 3, "max_block_summary_chars": 320, "max_wiki_summary_chars": 220,
        },
        "logging": {"level": "info", "log_to_file": False, "record_model_routing": True},
        "safety": {
            "protect_read_only_sources": True,
            "require_confirmation_for_destructive_actions": True,
            "deny_write_outside_allowed_paths": True,
            "allowed_write_paths": ["${IRIS_OUTPUT_DIR}", "./temp", "${IRIS_MEMORY_DIR}", "${IRIS_DATA_DIR}"],
        },
    }


@pytest.fixture
def minimal_llm_config() -> Dict[str, Any]:
    return {
        "version": "2.0",
        "default_strategy": {"default_model_role": "base_model", "fallback_model_role": "adv_model",
                             "prefer_lower_cost": True, "allow_auto_upgrade": True, "allow_auto_downgrade": True},
        "models": {
            "base_model": {
                "enabled": True, "default_model_id": "test-model",
                "models": {
                    "test-model": {
                        "provider": "openai_compatible", "model": "test", "display_name": "Test",
                        "multimodal": False, "max_context_tokens": 4096, "temperature": 0.2,
                        "timeout_seconds": 10, "max_retries": 0, "priority": 10, "cost_level": "low",
                        "reasoning_level": "standard", "supported_inputs": ["text"],
                        "use_cases": ["qa"], "notes": "",
                        "api_base_url": "https://api.test.com/v1", "api_key": "${TEST_API_KEY}",
                    }
                },
            },
            "adv_model": {
                "enabled": True, "default_model_id": "test-adv",
                "models": {
                    "test-adv": {
                        "provider": "openai_compatible", "model": "test-adv", "display_name": "Test Adv",
                        "multimodal": True, "max_context_tokens": 8192, "temperature": 0.2,
                        "timeout_seconds": 30, "max_retries": 1, "priority": 10, "cost_level": "medium",
                        "reasoning_level": "advanced", "supported_inputs": ["text", "image"],
                        "use_cases": ["qa", "analysis"], "notes": "",
                        "api_base_url": "https://api.test.com/v1", "api_key": "${TEST_API_KEY}",
                    }
                },
            },
        },
        "routing": {
            "rules": [
                {"name": "qa-rule", "enabled": True, "priority": 1,
                 "match": {"task_type": "qa"}, "route_to": "base_model", "fallback_to": "adv_model"},
                {"name": "complex-rule", "enabled": True, "priority": 10,
                 "match": {"complexity": "complex"}, "route_to": "adv_model"},
            ]
        },
        "embedding": {"enabled": False, "model": "", "api_base_url": "", "api_key": ""},
    }


@pytest.fixture
def minimal_data_source_config() -> Dict[str, Any]:
    return {
        "version": "1.0",
        "default_source": "test_source",
        "sources": {
            "test_source": {
                "enabled": True, "name": "测试源", "path": "/tmp/test_source",
                "format": "markdown", "read_only": True, "recursive": True,
                "include_patterns": ["**/*.md"], "exclude_patterns": [],
                "follow_symlinks": False, "extract_metadata": True, "notes": "",
            }
        },
        "ingestion": {
            "scan_on_startup": True, "incremental_scan": True, "chunk_strategy": "markdown_section",
            "max_file_size_mb": 20, "encoding": "utf-8", "store_file_hash": True,
            "store_mtime": True, "max_chunk_chars": 1200, "max_preview_chars": 180,
        },
    }


@pytest.fixture
def config_bundle(temp_project, minimal_app_config, minimal_llm_config, minimal_data_source_config) -> ConfigBundle:
    """创建一个最小化的 ConfigBundle 实例。"""
    config_dir = temp_project / "config"
    for name, data in [("app", minimal_app_config), ("llm", minimal_llm_config),
                        ("data_source", minimal_data_source_config)]:
        (config_dir / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 创建 .env
    (temp_project / ".env").write_text("TEST_API_KEY=sk-test-key\n", encoding="utf-8")

    return load_config_bundle(temp_project)
