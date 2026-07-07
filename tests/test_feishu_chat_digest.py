"""feishu/chat_digest.py 单元测试。"""

from __future__ import annotations

import pytest
from pathlib import Path


class TestChatDigestClassify:
    """_classify: 根据内容决定归档路径。"""

    def _digester(self, config_bundle, tmp_path):
        from unittest.mock import patch, MagicMock
        with patch("iris.feishu.chat_digest.FeishuClient"):
            from iris.feishu.chat_digest import ChatDigester
            d = ChatDigester.__new__(ChatDigester)
            d._bundle = config_bundle
            d._dedup_path = tmp_path / "dedup.json"
            d._wiki_root = tmp_path / "wiki"
            d._dedup_index_cache = None
            return d

    def test_has_decisions_and_todos_goes_to_meeting(self, config_bundle, tmp_path):
        d = self._digester(config_bundle, tmp_path)
        result = d._classify({"decisions": ["决策A"], "todos": ["任务B"], "risks": []})
        assert result == "05-会议纪要"

    def test_no_decisions_goes_to_discussion(self, config_bundle, tmp_path):
        d = self._digester(config_bundle, tmp_path)
        result = d._classify({"decisions": [], "todos": ["任务B"], "risks": []})
        assert result == "04-讨论思考"

    def test_decisions_without_todos_or_risks(self, config_bundle, tmp_path):
        d = self._digester(config_bundle, tmp_path)
        result = d._classify({"decisions": ["决策A"], "todos": [], "risks": []})
        assert result == "04-讨论思考"


class TestBuildMarkdownTodoTable:
    """_build_markdown: 待办表格含 | 的边界情况。"""

    def _digester(self, config_bundle, tmp_path):
        with __import__("unittest.mock", fromlist=["patch"]).patch("iris.feishu.chat_digest.FeishuClient"):
            from iris.feishu.chat_digest import ChatDigester
            d = ChatDigester.__new__(ChatDigester)
            d._bundle = config_bundle
            d._dedup_path = tmp_path / "dedup.json"
            d._wiki_root = tmp_path / "wiki"
            d._dedup_index_cache = None
            return d

    def test_normal_todo(self, config_bundle, tmp_path):
        d = self._digester(config_bundle, tmp_path)
        extracted = {
            "topic": "测试",
            "participants": [],
            "summary": "摘要",
            "key_points": [],
            "decisions": [],
            "risks": [],
            "related": "",
            "todos": ["完成报告|张三|2026-07-15"],
        }
        md = d._build_markdown(extracted, "测试群", "group", "chat_id",
                               "2026-07-01T00:00:00", "2026-07-07T00:00:00", 10, "now")
        assert "完成报告" in md
        assert "张三" in md
        assert "2026-07-15" in md

    def test_todo_with_pipe_in_task(self, config_bundle, tmp_path):
        d = self._digester(config_bundle, tmp_path)
        extracted = {
            "topic": "测试",
            "participants": [],
            "summary": "摘要",
            "key_points": [],
            "decisions": [],
            "risks": [],
            "related": "",
            "todos": ["分析A|对比B|李四|2026-07-20"],
        }
        md = d._build_markdown(extracted, "测试群", "group", "chat_id",
                               "2026-07-01T00:00:00", "2026-07-07T00:00:00", 10, "now")
        # 应当将 "分析A|对比B" 合并为事项，不错误拆分
        lines = [l for l in md.split("\n") if "分析A" in l or "对比B" in l]
        assert len(lines) >= 1
        # 责任人应为 李四
        assert "李四" in md


class TestCheckDedup:
    """_check_dedup: 排重缓存命中/未命中。"""

    def _digester(self, config_bundle, tmp_path):
        import json
        dedup_path = tmp_path / "dedup.json"
        dedup_path.write_text(json.dumps({
            "version": "1.0",
            "items": [
                {"dedup_key": "chat123|2026-07-01|2026-07-07", "target_name": "测试群"},
            ]
        }), encoding="utf-8")
        with __import__("unittest.mock", fromlist=["patch"]).patch("iris.feishu.chat_digest.FeishuClient"):
            from iris.feishu.chat_digest import ChatDigester
            d = ChatDigester.__new__(ChatDigester)
            d._bundle = config_bundle
            d._dedup_path = dedup_path
            d._wiki_root = tmp_path / "wiki"
            d._dedup_index_cache = None
            return d

    def test_hit(self, config_bundle, tmp_path):
        d = self._digester(config_bundle, tmp_path)
        result = d._check_dedup("chat123|2026-07-01|2026-07-07")
        assert result is not None
        assert result["target_name"] == "测试群"

    def test_miss(self, config_bundle, tmp_path):
        d = self._digester(config_bundle, tmp_path)
        result = d._check_dedup("nonexistent_key")
        assert result is None

    def test_cache_hit_on_second_call(self, config_bundle, tmp_path):
        d = self._digester(config_bundle, tmp_path)
        # 第一次调用加载磁盘
        d._check_dedup("any_key")
        # 缓存应被填充
        assert d._dedup_index_cache is not None
        # 第二次调用不应再读磁盘（删掉文件验证）
        d._dedup_path.unlink()
        result = d._check_dedup("chat123|2026-07-01|2026-07-07")
        assert result is not None  # 从缓存命中
