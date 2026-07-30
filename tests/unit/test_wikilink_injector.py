"""iris.wiki.wikilink_injector 模块单元测试。"""

from __future__ import annotations

import pytest
from pathlib import Path

from iris.wiki.wikilink_injector import WikilinkInjector


# ── 测试辅助：创建临时 Wiki 目录结构 ──────────────────────


def _make_wiki_page(tmp_path: Path, type_dir: str, filename: str,
                    fm_title: str) -> Path:
    """在临时目录中创建一个 Wiki 页面。"""
    type_path = tmp_path / type_dir
    type_path.mkdir(parents=True, exist_ok=True)
    content = f"---\ntitle: {fm_title}\ntype: project\n---\n\n# {fm_title}\n\n正文内容。"
    filepath = type_path / filename
    filepath.write_text(content, encoding="utf-8")
    return filepath


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    """创建一个包含 4 种类型页面的临时 Wiki 根目录。"""
    root = tmp_path / "LLM-WIKI"
    root.mkdir()
    _make_wiki_page(root, "01-领域", "领域-AI质检.md", "AI质检")
    _make_wiki_page(root, "02-概念", "概念-Iris.md", "Iris")
    _make_wiki_page(root, "03-项目",
                    "项目-XRay手机拆修检测项目.md", "XRay手机拆修检测项目")
    _make_wiki_page(root, "03-项目",
                    "项目-拍照3.0 AI外观定级项目.md", "拍照3.0 AI外观定级项目")
    _make_wiki_page(root, "04-人物", "人物-冯扬.md", "冯扬")
    _make_wiki_page(root, "04-人物", "人物-卞凯.md", "卞凯")
    _make_wiki_page(root, "04-人物", "人物-李光明.md", "李光明")
    return root


@pytest.fixture
def injector(wiki_root: Path) -> WikilinkInjector:
    """基于临时 Wiki 目录创建注入器。"""
    return WikilinkInjector(wiki_root)


class TestWikilinkInjectorIndex:
    """WikilinkInjector 索引构建测试。"""

    def test_build_index(self, wiki_root: Path):
        """扫描 Wiki 目录构建标题索引。"""
        injector = WikilinkInjector(wiki_root)
        titles = injector.get_title_index()
        assert len(titles) > 0
        # 验证 frontmatter title 被索引
        assert "冯扬" in titles
        assert titles["冯扬"] == "04-人物/人物-冯扬"
        assert "XRay手机拆修检测项目" in titles

    def test_index_relative_path_format(self, wiki_root: Path):
        """索引使用 type_dir/filename 格式的路径。"""
        injector = WikilinkInjector(wiki_root)
        titles = injector.get_title_index()
        for title, path in titles.items():
            assert "/" in path or "\\" in path
            assert not path.endswith(".md")

    def test_file_title_from_stem(self, wiki_root: Path):
        """文件名去掉类型前缀后也可作为标题。"""
        injector = WikilinkInjector(wiki_root)
        titles = injector.get_title_index()
        # 文件名 "项目-XRay手机拆修检测项目" → 标题 "XRay手机拆修检测项目"
        assert "XRay手机拆修检测项目" in titles
        # 文件名 "人物-冯扬" → 标题 "冯扬"
        assert "冯扬" in titles

    def test_missing_wiki_root(self, tmp_path: Path):
        """Wiki 根目录不存在时不抛异常。"""
        nonexistent = tmp_path / "nonexistent"
        injector = WikilinkInjector(nonexistent)
        assert injector.get_title_index() == {}

    def test_refresh(self, wiki_root: Path):
        """refresh() 重新扫描并更新索引。"""
        injector = WikilinkInjector(wiki_root)
        old_count = len(injector.get_title_index())

        # 添加新页面
        _make_wiki_page(wiki_root, "04-人物", "人物-新人物.md", "新人物")
        injector.refresh()
        new_count = len(injector.get_title_index())
        assert new_count > old_count
        assert "新人物" in injector.get_title_index()


class TestWikilinkInjectorInject:
    """WikilinkInjector.inject() 测试。"""

    def test_inject_person_name(self, injector: WikilinkInjector):
        """正文中的人名被替换为 wikilink。"""
        content = "# 会议纪要\n\n冯扬提出了新的方案。"
        result = injector.inject(content)
        assert "[[04-人物/人物-冯扬]]" in result

    def test_inject_project_name(self, injector: WikilinkInjector):
        """正文中的项目名被替换为 wikilink。"""
        content = "XRay手机拆修检测项目本周上线了新模型。"
        result = injector.inject(content)
        assert "XRay手机拆修检测项目" not in result or \
            "[[" in result

    def test_only_first_occurrence(self, injector: WikilinkInjector):
        """同一实体仅首次出现被替换。"""
        content = "冯扬和冯扬讨论了这个问题。"
        result = injector.inject(content)
        # 检查只有一个 [[wikilink]]
        link_count = result.count("[[04-人物/人物-冯扬]]")
        assert link_count == 1
        # 第二次出现保持原文
        assert result.count("冯扬") >= 1  # 第二次出现保持 "冯扬"

    def test_skip_existing_wikilinks(self, injector: WikilinkInjector):
        """已有 wikilink 中的文本不被重复替换。"""
        content = "[[04-人物/人物-冯扬|冯扬]] 参与了讨论。"
        result = injector.inject(content)
        # 已有 wikilink 被保护，不会生成新的重复链接
        assert result.count("[[04-人物/人物-冯扬]]") <= 1

    def test_skip_code_blocks(self, injector: WikilinkInjector):
        """代码块内的文本不被替换。"""
        content = """
代码块内容：

```
这里提到了冯扬
```

正文中首次提到冯扬。
"""
        result = injector.inject(content)
        # 正文中的 "冯扬" 被替换
        assert result.count("[[04-人物/人物-冯扬]]") >= 1
        # 代码块内的 "冯扬" 保持原样（在 "```" 标记块内）
        # 验证代码块内没有 wikilink
        lines = result.split("\n")
        in_code = False
        for line in lines:
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                assert "[[" not in line

    def test_skip_inline_code(self, injector: WikilinkInjector):
        """行内代码中的文本不被替换。"""
        content = "调用 `冯扬` 函数。冯扬是项目负责人。"
        result = injector.inject(content)
        # 行内代码中的 "冯扬" 不受影响
        assert "`冯扬`" in result
        # 正文中的 "冯扬" 被替换
        assert "[[04-人物/人物-冯扬]]" in result

    def test_longest_match_first(self, injector: WikilinkInjector):
        """长标题优先匹配，避免短标题的误匹配。"""
        # "XRay手机拆修检测项目" 比 "XRay" 长
        # 确保长标题完整匹配
        content = "XRay手机拆修检测项目有新进展。"
        result = injector.inject(content)
        # 整个长标题应被匹配（作为单个 wikilink）
        target = injector.get_title_index().get("XRay手机拆修检测项目", "")
        if target:
            assert f"[[{target}]]" in result

    def test_no_match_no_change(self, injector: WikilinkInjector):
        """没有已知实体时返回原内容。"""
        content = "这是一段完全不包含任何已知实体的文本。"
        result = injector.inject(content)
        assert result == content

    def test_exclude_titles(self, injector: WikilinkInjector):
        """exclude_titles 参数排除指定标题。"""
        content = "冯扬提交了周报。"
        result = injector.inject(content, exclude_titles={"冯扬"})
        # 冯扬被排除，不应被替换
        assert "[[04-人物/人物-冯扬]]" not in result

    def test_skip_urls(self, injector: WikilinkInjector):
        """URL 中的文本不被替换。"""
        # 创建一个 URL 包含可能会被误匹配的文本
        content = "参考 https://example.com/冯扬/profile 了解更多。冯扬是项目负责人。"
        result = injector.inject(content)
        # URL 中的 "冯扬" 不受影响
        assert "https://example.com/冯扬/profile" in result

    def test_skip_markdown_links(self, injector: WikilinkInjector):
        """Markdown 链接中的文本不被替换。"""
        content = "查看[冯扬的文档](path/to/doc)。冯扬也参与了讨论。"
        result = injector.inject(content)
        # 已有 markdown 链接中的 "冯扬" 受保护
        assert "[冯扬的文档](path/to/doc)" in result

    def test_empty_content(self, injector: WikilinkInjector):
        """空内容不抛异常。"""
        result = injector.inject("")
        assert result == ""


class TestWikilinkInjectorHelper:
    """WikilinkInjector 辅助方法测试。"""

    def test_extract_title_from_filename(self):
        """_extract_title_from_filename 正确去掉类型前缀。"""
        f = WikilinkInjector._extract_title_from_filename
        assert f("项目-XRay检测项目") == "XRay检测项目"
        assert f("人物-冯扬") == "冯扬"
        assert f("概念-Iris") == "Iris"
        assert f("领域-AI质检") == "AI质检"
        # 无前缀时保持原样
        assert f("普通文件名") == "普通文件名"

    def test_replace_first(self):
        """_replace_first 仅替换首次出现。"""
        result = WikilinkInjector._replace_first("a b a b a", "a", "X")
        assert result == "X b a b a"

    def test_merge_regions(self):
        """_merge_regions 合并重叠区间。"""
        regions = [(0, 5, "hello"), (3, 8, "lo wo"), (10, 12, "x")]
        merged = WikilinkInjector._merge_regions(regions)
        # (0,5) 和 (3,8) 重叠 → 合并为 (0,8)
        assert merged[0][0] == 0
        assert merged[0][1] == 8
        # (10,12) 独立
        assert merged[1] == (10, 12, "x")

    def test_find_safe_skips_wikilink_markers(self):
        """_find_safe 跳过 \x01...\x02 标记区域，不匹配内部文本。"""
        # 模拟已注入 wikilink 的内容：\x01target-path\x02 followed by text
        text_with_wl = "\x0103-项目/项目-XRay手机拆修检测\x02 正文"
        # 搜索 "XRay" — 它在 wikilink target 路径内部，应被跳过
        idx = WikilinkInjector._find_safe(text_with_wl, "XRay")
        assert idx == -1  # 在标记区域内，不应匹配

    def test_find_safe_finds_outside_markers(self):
        """_find_safe 在标记区域外的文本中正常匹配。"""
        text = "\x0103-项目/项目-XRay手机拆修检测\x02 正文中提到了 XRay。"
        idx = WikilinkInjector._find_safe(text, "XRay")
        # 第二个 XRay 在正文中（标记区域外），应被找到
        assert idx > 10  # 在较后的位置

    def test_nested_wikilink_prevention(self, wiki_root: Path):
        """注入长标题后，短标题不会匹配到 wikilink target 路径内部。"""
        # 创建两个标题，短标题是长标题 target 路径的子串
        # 例如: "搜索推荐系统" → target "03-项目/项目-搜索推荐系统"
        #       短标题 "搜索推荐" 不应匹配到 target 路径内的 "搜索推荐"
        injector = WikilinkInjector(wiki_root)
        titles = injector.get_title_index()
        # 找一对父子串标题
        long_titles = sorted(titles.keys(), key=len, reverse=True)
        content_parts = []
        for t in long_titles[:5]:
            content_parts.append(f"{t}相关讨论。")
        content = " ".join(content_parts)
        result = injector.inject(content)
        # 不应出现嵌套的 [[...[[...]]...]]
        import re
        nested = re.findall(r'\[\[[^\]]*\[\[', result)
        assert len(nested) == 0, f"发现嵌套 wikilink: {nested}"
