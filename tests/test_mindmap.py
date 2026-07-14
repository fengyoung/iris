"""analysis/mindmap.py 纯函数与降级路径测试。

覆盖：JSON 树解析、XMind 字节生成、dict→树递归、Mermaid 降级渲染。
不触发真实 LLM。
"""

from __future__ import annotations

import io
import zipfile

from iris.analysis.mindmap import (
    MindmapNode,
    _build_mermaid_mindmap,
    _build_xmind_bytes,
    _dict_to_tree,
    _parse_json_tree,
)


class TestParseJsonTree:
    def test_parses_clean_json(self):
        result = _parse_json_tree('{"title": "根", "children": []}')
        assert result["title"] == "根"

    def test_extracts_braced_fragment(self):
        result = _parse_json_tree('说明：{"title": "根"} 结束')
        assert result.get("title") == "根"

    def test_returns_error_on_unparseable(self):
        result = _parse_json_tree("完全没有结构")
        assert "error" in result
        assert "raw_text" in result


class TestDictToTree:
    def test_list_children(self):
        data = {"note": "n", "children": [{"title": "子1"}, {"title": "子2"}]}
        tree = _dict_to_tree(data, "根")
        assert tree.title == "根"
        assert tree.note == "n"
        assert [c.title for c in tree.children] == ["子1", "子2"]

    def test_name_fallback_when_no_title(self):
        data = {"children": [{"name": "用name"}]}
        tree = _dict_to_tree(data)
        assert tree.children[0].title == "用name"

    def test_dict_children_with_scalar_values(self):
        data = {"children": {"分支A": "叶子值", "分支B": {"title": "子字典"}}}
        tree = _dict_to_tree(data)
        titles = {c.title for c in tree.children}
        assert "叶子值" in titles
        assert "分支B" in titles

    def test_nested_recursion(self):
        data = {"children": [{"title": "L1", "children": [{"title": "L2"}]}]}
        tree = _dict_to_tree(data)
        assert tree.children[0].children[0].title == "L2"


class TestBuildXmindBytes:
    def test_produces_valid_zip_with_content_xml(self):
        tree = MindmapNode(title="根", children=[MindmapNode(title="子")])
        data = _build_xmind_bytes(tree)
        assert isinstance(data, bytes)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            assert "content.xml" in names
            assert "META-INF/manifest.xml" in names
            content = zf.read("content.xml").decode("utf-8")
            assert "根" in content
            assert "子" in content


class TestBuildMermaidMindmap:
    def test_renders_groups(self):
        structured = {
            "ordered_groups": ["进展"],
            "groups": {"进展": [{"summary": "做了A"}]},
        }
        out = _build_mermaid_mindmap("我的查询", [], structured)
        assert out.startswith("mindmap")
        assert "root((我的查询))" in out
        assert "进展" in out
        assert "做了A" in out

    def test_no_groups_still_renders_root(self):
        # structured 无分组时至少输出 mindmap 头与 root 节点，不崩溃
        blocks = [{"title": "块标题1"}, {"title": "块标题2"}]
        out = _build_mermaid_mindmap("查询", blocks, {"ordered_groups": [], "groups": {}})
        assert out.splitlines()[0] == "mindmap"
        assert "root((查询))" in out
