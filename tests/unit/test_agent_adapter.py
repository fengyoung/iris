"""core/agent_adapter.py 测试 — 覆盖 AgentCapability 数据类 & IRIS_CAPABILITIES。"""

from __future__ import annotations

from iris.core.agent_adapter import AgentCapability, IRIS_CAPABILITIES


class TestAgentCapability:
    def test_construct_minimal(self):
        cap = AgentCapability(name="test", description="测试", command="test-cmd")
        assert cap.name == "test"
        assert cap.description == "测试"
        assert cap.command == "test-cmd"
        assert cap.input_schema == {}
        assert cap.output_schema == {}
        assert cap.tags == []

    def test_construct_with_schemas(self):
        cap = AgentCapability(
            name="search",
            description="搜索",
            command="search",
            input_schema={"query": {"type": "string"}},
            output_schema={"hits": {"type": "array"}},
            tags=["retrieval"],
        )
        assert cap.input_schema["query"]["type"] == "string"
        assert cap.tags == ["retrieval"]

    def test_frozen_dataclass(self):
        cap = AgentCapability(name="frozen", description="不可变", command="freeze")
        # frozen dataclass 不可修改
        try:
            cap.name = "modified"
            assert False, "应为 frozen"
        except Exception:
            pass


class TestIrisCapabilities:
    def test_non_empty(self):
        assert len(IRIS_CAPABILITIES) >= 15

    def test_all_have_required_fields(self):
        for cap in IRIS_CAPABILITIES:
            assert cap.name, f"{cap} 缺少 name"
            assert cap.command, f"{cap} 缺少 command"
            assert isinstance(cap.tags, list), f"{cap} tags 非列表"

    def test_unique_names(self):
        names = [c.name for c in IRIS_CAPABILITIES]
        assert len(names) == len(set(names)), f"能力名重复: {names}"

    def test_unique_commands(self):
        commands = [c.command for c in IRIS_CAPABILITIES]
        assert len(commands) == len(set(commands)), f"命令重复: {commands}"

    def test_core_capabilities_present(self):
        names = {c.name for c in IRIS_CAPABILITIES}
        core = {"search", "ask", "scan-source", "build-chunks", "daily-start", "diagnose"}
        missing = core - names
        assert not missing, f"缺少核心能力: {missing}"

    def test_tags_no_typos(self):
        """标签值应是已知类别。"""
        known_tags = {"data", "scan", "chunk", "vector", "retrieval", "read-only",
                      "qa", "llm", "multimodal", "wiki", "maintenance", "diagnostic",
                      "report", "meeting", "batch", "memory", "context", "system"}
        for cap in IRIS_CAPABILITIES:
            for tag in cap.tags:
                assert tag in known_tags, f"未知标签 '{tag}' in {cap.name}"
