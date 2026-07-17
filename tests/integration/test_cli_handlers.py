"""CLI 集成测试 — 通过 handler 函数直接调用测试 _data/_system/_wiki 处理器。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris.app.cli.handlers import (
    handle_check_config,
    handle_route_model,
    handle_status,
    handle_agent_spec,
    handle_diagnose,
    handle_memory_status,
    handle_memory_list,
    handle_working_set,
    handle_working_show,
    handle_working_clear,
    handle_scan_source,
    handle_search,
    handle_wiki_lint,
    handle_build_wiki_nav,
    handle_build_graph,
    handle_graph_query,
    handle_usage_stats,
)
from iris.utils.logging import IrisLogger


# ── 辅助函数 ──────────────────────────────────────────────

def _make_args(**kwargs):
    """创建模拟 argparse Namespace，包含所有 CLI 参数默认值。"""
    defaults = {
        "command": kwargs.get("command", "test"),
        "project_root": ".", "workspace": "", "context": "{}",
        "source": "", "query": "", "top_k": 5, "mode": "local",
        "pretty": False, "write": True, "overwrite": False, "backup": False,
        "incremental": False, "limit": 20,
        "output_file": "", "input_file": "", "image": "",
        "memory_type": "all", "concept": "", "replace": False,
        "age_days": 90, "auto_age": False,
        "task": "", "pending": "", "add_pending": "", "change": "", "add_change": "",
        "notes": "", "summary_only": False, "write_summary": False,
        "review_file": "", "batch_file": "", "export_jsonl": "",
        "export_review": "", "export_review_md": "",
        "page_filter": "", "sample_rate": 0.3,
        "full": False, "op": "", "node": "", "to": "", "hops": 1, "min_degree": 3,
        "by": "month", "cost": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _capture_output(handler, args, bundle, logger, capsys):
    """执行 handler 并捕获 stdout 输出。"""
    code = handler(args, bundle, logger)
    out = capsys.readouterr().out.strip()
    # 尝试解析 JSON，非 JSON 输出返回空 dict
    payload = {}
    if out and out.startswith("{"):
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            pass
    return code, out, payload


def _make_wiki_root(tmp_path, pages=1):
    """创建含测试页面的 Wiki 目录。"""
    wiki_root = tmp_path / "LLM-WIKI"
    (wiki_root / "01-领域").mkdir(parents=True)
    (wiki_root / "02-概念").mkdir(parents=True)
    if pages >= 1:
        (wiki_root / "01-领域" / "领域-搜索.md").write_text(
            "---\ntitle: 搜索\ntype: domain\nstatus: stable\ntags: [搜索, AI]\n---\n"
            "## 摘要\n搜索领域概述。[[排序]]", encoding="utf-8")
    if pages >= 2:
        (wiki_root / "02-概念" / "概念-排序.md").write_text(
            "---\ntitle: 排序\ntype: concept\nstatus: stable\ntags: [排序]\n---\n"
            "## 摘要\n排序算法基础。[[搜索]]", encoding="utf-8")
    return wiki_root


# ── _data handlers ───────────────────────────────────────


class TestCheckConfig:
    def test_returns_zero(self, config_bundle):
        args = _make_args()
        logger = IrisLogger(config_bundle)
        code = handle_check_config(args, config_bundle, logger)
        assert code == 0


class TestRouteModel:
    def test_default_context(self, config_bundle, capsys):
        args = _make_args(context='{"input_type": "text"}')
        code, out, payload = _capture_output(
            handle_route_model, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0
        assert "selected_role" in payload

    def test_multimodal_context(self, config_bundle, capsys):
        args = _make_args(context='{"input_type": "multimodal"}')
        code, out, payload = _capture_output(
            handle_route_model, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0


class TestScanSource:
    def test_returns_zero(self, config_bundle, capsys):
        args = _make_args(summary_only=True)
        code, out, payload = _capture_output(
            handle_scan_source, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0

    def test_with_source_name(self, config_bundle, capsys, temp_project):
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir(exist_ok=True)
        (source_dir / "test.md").write_text("# Test", encoding="utf-8")
        args = _make_args(source="test_source", summary_only=True)
        code, out, payload = _capture_output(
            handle_scan_source, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0


class TestSearch:
    def test_returns_zero(self, config_bundle, capsys):
        args = _make_args(query="测试查询")
        code, out, payload = _capture_output(
            handle_search, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0

    def test_empty_query(self, config_bundle, capsys):
        args = _make_args(query="")
        code, out, payload = _capture_output(
            handle_search, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0


# ── _system handlers ─────────────────────────────────────


class TestStatus:
    def test_returns_dict(self, config_bundle, capsys):
        args = _make_args()
        code, out, payload = _capture_output(
            handle_status, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0


class TestDiagnose:
    def test_returns_zero(self, config_bundle, capsys):
        args = _make_args()
        code, out, payload = _capture_output(
            handle_diagnose, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0


class TestAgentSpec:
    def test_returns_spec(self, config_bundle, capsys):
        args = _make_args()
        code, out, payload = _capture_output(
            handle_agent_spec, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0


class TestMemoryStatus:
    def test_returns_status(self, config_bundle, capsys):
        args = _make_args()
        code, out, payload = _capture_output(
            handle_memory_status, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0

    def test_list_all(self, config_bundle, capsys):
        args = _make_args(memory_type="all")
        code, out, payload = _capture_output(
            handle_memory_list, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0

    def test_list_profile(self, config_bundle, capsys):
        args = _make_args(memory_type="profile")
        code, out, payload = _capture_output(
            handle_memory_list, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0


class TestWorkingContext:
    def test_set_and_clear(self, config_bundle, capsys):
        logger = IrisLogger(config_bundle)
        # Set working context
        args = _make_args(command="working-set", task="测试任务",
                          pending="任务A|任务B", notes="备注")
        code = handle_working_set(args, config_bundle, logger)
        assert code == 0

        # Show should succeed
        capsys.readouterr()  # flush
        code = handle_working_show(_make_args(command="working-show"), config_bundle, logger)
        assert code == 0

        # Clear
        code = handle_working_clear(_make_args(command="working-clear"), config_bundle, logger)
        assert code == 0


# ── _wiki handlers ───────────────────────────────────────


class TestWikiLint:
    def test_empty_wiki(self, config_bundle, capsys, temp_project):
        wiki_root = _make_wiki_root(temp_project, pages=0)

        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project, app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "t",
                "sources": {"t": {"path": str(temp_project)}}},
            llm_dict={}, wiki_dict={"wiki_root": str(wiki_root)},
        )
        args = _make_args()
        code, out, payload = _capture_output(
            handle_wiki_lint, args, bundle, IrisLogger(bundle), capsys)
        assert code == 0

    def test_with_pages(self, config_bundle, capsys, temp_project):
        wiki_root = _make_wiki_root(temp_project, pages=2)

        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project, app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "t",
                "sources": {"t": {"path": str(temp_project)}}},
            llm_dict={}, wiki_dict={"wiki_root": str(wiki_root)},
        )
        args = _make_args()
        code, out, payload = _capture_output(
            handle_wiki_lint, args, bundle, IrisLogger(bundle), capsys)
        assert code == 0


class TestBuildWikiNav:
    def test_creates_index(self, config_bundle, capsys, temp_project):
        wiki_root = _make_wiki_root(temp_project, pages=2)

        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project, app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "t",
                "sources": {"t": {"path": str(temp_project)}}},
            llm_dict={}, wiki_dict={"wiki_root": str(wiki_root)},
        )
        args = _make_args()
        code, out, payload = _capture_output(
            handle_build_wiki_nav, args, bundle, IrisLogger(bundle), capsys)
        assert code == 0
        index_path = wiki_root / "index.md"
        assert index_path.exists()


# ── _data: graph handlers ────────────────────────────────


class TestGraphHandlers:
    def test_build_graph_on_wiki(self, config_bundle, capsys, temp_project):
        wiki_root = _make_wiki_root(temp_project, pages=2)

        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project, app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "t",
                "sources": {"t": {"path": str(temp_project)}}},
            llm_dict={}, wiki_dict={"wiki_root": str(wiki_root)},
        )
        args = _make_args()
        code, out, payload = _capture_output(
            handle_build_graph, args, bundle, IrisLogger(bundle), capsys)
        assert code == 0

    def test_graph_query_after_build(self, config_bundle, capsys, temp_project):
        wiki_root = _make_wiki_root(temp_project, pages=2)

        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project, app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "t",
                "sources": {"t": {"path": str(temp_project)}}},
            llm_dict={}, wiki_dict={"wiki_root": str(wiki_root)},
        )
        logger = IrisLogger(bundle)

        # Build graph first (flush stdout)
        handle_build_graph(_make_args(), bundle, logger)
        capsys.readouterr()

        # Query neighbors
        args = _make_args(op="neighbors", node="搜索", hops=1, pretty=False)
        args.command = "graph-query"
        code, out, payload = _capture_output(
            handle_graph_query, args, bundle, logger, capsys)
        assert code == 0

    def test_graph_query_unknown_op(self, config_bundle, capsys, temp_project):
        wiki_root = _make_wiki_root(temp_project, pages=2)

        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project, app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "t",
                "sources": {"t": {"path": str(temp_project)}}},
            llm_dict={}, wiki_dict={"wiki_root": str(wiki_root)},
        )
        logger = IrisLogger(bundle)
        handle_build_graph(_make_args(), bundle, logger)
        capsys.readouterr()  # flush build output

        args = _make_args(op="unknown_op", node="搜索", pretty=False)
        args.command = "graph-query"
        code, out, payload = _capture_output(
            handle_graph_query, args, bundle, logger, capsys)
        assert code == 1
        assert "error" in payload


# ── usage-stats ──────────────────────────────────────────


class TestUsageStats:
    def test_returns_stats(self, config_bundle, capsys):
        args = _make_args()
        code, out, payload = _capture_output(
            handle_usage_stats, args, config_bundle, IrisLogger(config_bundle), capsys)
        assert code == 0
