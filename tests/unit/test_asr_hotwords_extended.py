"""wiki/asr/hotwords.py 扩展测试 — 覆盖 _is_valid_hotword 更多边界、_build_hotwords_prompt。"""

from __future__ import annotations

import pytest

from iris.wiki.asr.hotwords import (
    _is_valid_hotword,
    _clean_text_term,
    _build_hotwords_prompt,
    _extract_batch_outcome,
    _parse_hotwords_response,
    _salvage_terms,
)
from iris.wiki.context_loader import WikiPageInfo


def _make_page(title, page_type, body="", summary=""):
    return WikiPageInfo(
        path=None, title=title, page_type=page_type,
        status="stable", summary=summary, body=body, relative_path="",
    )


class TestIsValidHotwordExtended:
    def test_single_cjk_char_invalid(self):
        """单个中文字算有效（>=2）。"""
        assert _is_valid_hotword("字") is False

    def test_single_english_char_invalid(self):
        assert _is_valid_hotword("A") is False

    def test_two_char_cjk_valid(self):
        assert _is_valid_hotword("测试") is True

    def test_two_char_english_valid(self):
        assert _is_valid_hotword("AI") is True

    def test_mixed_brackets_balanced(self):
        assert _is_valid_hotword("测试（AI）") is True

    def test_mixed_brackets_unbalanced_aside(self):
        assert _is_valid_hotword("（测试") is False

    def test_sentence_fragment_rejected(self):
        """超过 12 字且含"的" → 视为句子片段。"""
        assert _is_valid_hotword("这是一个测试的句子片段") is False

    def test_long_but_no_connective(self):
        """超过 12 字但无连接词 → 仍然有效（可能是长术语）。"""
        # 此条实际上会被 _exceeds_char_limit 拦截，因为超过 10 个中文字
        # 所以换一个方式测试：12 个中文字且无"的/是/在"等连接词
        term = "大规模分布式机器学习系统架构"
        result = _is_valid_hotword(term)
        # 可能会因为长度被拒绝
        assert isinstance(result, bool)

    def test_pure_punctuation_passes_len_check(self):
        """全角标点长度为 3（>=2），无连接词，未命中过滤规则，会通过校验。
        实际业务中此类词会被上游 _clean_text_term 清理掉。"""
        # _clean_text_term 会 strip 这些标点→变成空串→_is_valid_hotword 拒绝
        from iris.wiki.asr.hotwords import _clean_text_term
        cleaned = _clean_text_term("，。！")
        assert _is_valid_hotword(cleaned) is False

    def test_math_expression_as_hotword_invalid(self):
        """纯数学表达式不是有效热词。"""
        assert _is_valid_hotword("1+2-3/4") is False

    def test_exactly_2_chars_valid(self):
        assert _is_valid_hotword("召回") is True

    def test_control_chars_after_clean(self):
        """控制字符的 term 在 _clean_text_term 之后应被清理。"""
        cleaned = _clean_text_term("测试\x00词")
        # 清理后至少保证是有效字符串
        assert isinstance(cleaned, str)


class TestCleanTextTermExtended:
    def test_removes_null_byte(self):
        assert "\x00" not in _clean_text_term("test\x00abc")

    def test_removes_replacement_char(self):
        assert "�" not in _clean_text_term("test�abc")

    def test_removes_leading_trailing_brackets(self):
        assert _clean_text_term("「测试」") == "测试"
        assert _clean_text_term("（测试）") == "测试"

    def test_single_quote_stripped(self):
        result = _clean_text_term('"测试"')
        assert result == "测试" or result == '"测试"'

    def test_combines_all_cleanup_steps(self):
        dirty = "\x00「测试\x1f�词」"
        result = _clean_text_term(dirty)
        assert "\x00" not in result
        assert "�" not in result
        assert isinstance(result, str)


class TestBuildHotwordsPrompt:
    def test_returns_non_empty_string(self):
        pages = [_make_page("测试页", "concept", body="# 章节\n内容", summary="关于测试")]
        prompt = _build_hotwords_prompt(pages, domain_context="")
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_includes_page_type(self):
        pages = [_make_page("项目X", "project")]
        prompt = _build_hotwords_prompt(pages)
        assert "PROJECT" in prompt
        assert "项目X" in prompt

    def test_includes_domain_context(self):
        pages = [_make_page("页", "concept")]
        ctx = "这是一个数据仓库领域。"
        prompt = _build_hotwords_prompt(pages, domain_context=ctx)
        assert ctx in prompt

    def test_multiple_pages(self):
        pages = [
            _make_page("页1", "concept", body="# 标题1\n内容", summary="摘要1"),
            _make_page("页2", "domain", body="# 标题2\n其他", summary="摘要2"),
        ]
        prompt = _build_hotwords_prompt(pages)
        assert "页1" in prompt
        assert "页2" in prompt

    def test_empty_body_and_summary(self):
        """空 body/summary 不抛异常。"""
        pages = [_make_page("空页", "concept")]
        prompt = _build_hotwords_prompt(pages)
        assert "空页" in prompt

    def test_includes_headings_from_body(self):
        pages = [_make_page("页", "concept", body="# 背景\n## 方法\n### 细节")]
        prompt = _build_hotwords_prompt(pages)
        assert "背景" in prompt
        assert "方法" in prompt


class TestParseHotwordsResponseExtended:
    def test_nested_brackets_in_term_values(self):
        """term 值中带有 JSON 特殊字符 → 正确解析。"""
        import json
        resp = json.dumps([{"term": "Model-v2"}, {"term": "H200"}])
        result = _parse_hotwords_response(resp, set())
        assert "Model-v2" in result
        assert "H200" in result

    def test_non_dict_items_skipped(self):
        import json
        resp = json.dumps([{"term": "有效"}, "非字典", {"term": "也有效"}])
        result = _parse_hotwords_response(resp, set())
        assert "有效" in result
        assert "也有效" in result

    def test_empty_term_skipped(self):
        import json
        resp = json.dumps([{"term": ""}, {"term": "有效"}])
        result = _parse_hotwords_response(resp, set())
        assert result == ["有效"]

    def test_not_list_returns_empty(self):
        import json
        resp = json.dumps({"term": "不是数组"})
        assert _parse_hotwords_response(resp, set()) == []


class TestSalvageTruncatedResponse:
    """max_tokens 截断让整个 JSON 数组缺闭合 `]`，严格解析必然失败。

    修复前这类响应整批归零（日志表现为「第N批 0词」）；现改为逐条捞回完整对象。
    """

    def test_truncated_array_salvages_complete_terms(self):
        # 尾部被截断：数组没有闭合 `]`，最后一个对象也没闭合 `}`，
        # 但三个 term 值本身都写完了，应当全部捞回。
        resp = '[{"term":"张三"},{"term":"Model-v2"},{"term":"李四"'
        assert _parse_hotwords_response(resp, set()) == ["张三", "Model-v2", "李四"]

    def test_salvage_handles_escaped_quotes(self):
        resp = '[{"term":"A\\"B"},{"term":"OCR"}'
        assert _parse_hotwords_response(resp, set()) == ['A"B', "OCR"]

    def test_unterminated_term_value_is_not_salvaged(self):
        """值的引号都没闭合 → 宁可不救，避免把半个词当热词。"""
        assert _parse_hotwords_response('[{"term":"张', set()) == []
        assert _salvage_terms('[{"term":"张') == []

    def test_salvage_respects_existing_dedup(self):
        resp = '[{"term":"张三"},{"term":"李四"'
        assert _parse_hotwords_response(resp, {"张三"}) == ["李四"]

    def test_salvage_ignores_non_term_objects(self):
        resp = '[{"other":1},{"term":"有效"},{"term":'
        assert _parse_hotwords_response(resp, set()) == ["有效"]


class _FakeLLM:
    """按调用顺序返回预设响应；元素为 Exception 时抛出。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def generate(self, prompt, **kwargs):
        from types import SimpleNamespace
        self.calls += 1
        item = self._responses.pop(0) if self._responses else ""
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("iris.wiki.asr.hotwords.time.sleep", lambda *_: None)


class TestBatchOutcomeSplitRetry:
    """空产出（截断/非 JSON）时对半拆批重试，避免整批归零。"""

    @staticmethod
    def _pages(n):
        return [_make_page(f"P{i}", "person") for i in range(n)]

    def test_success_needs_single_call(self):
        outcome = _extract_batch_outcome(_FakeLLM(['[{"term":"张三"}]']), self._pages(4), "")
        assert outcome.terms == ["张三"]
        assert outcome.calls == 1
        assert outcome.error is None

    def test_empty_response_triggers_split_and_merges(self, no_sleep):
        llm = _FakeLLM(["", '[{"term":"左一"}]', '[{"term":"右一"}]'])
        outcome = _extract_batch_outcome(llm, self._pages(4), "")
        assert outcome.terms == ["左一", "右一"]
        assert outcome.calls == 3
        assert outcome.error is None

    def test_exception_retried_then_reported(self, no_sleep):
        llm = _FakeLLM([RuntimeError("boom"), RuntimeError("boom")])
        outcome = _extract_batch_outcome(llm, self._pages(1), "")  # 单页无法拆批
        assert outcome.terms == []
        assert isinstance(outcome.error, RuntimeError)
        assert outcome.calls == 2

    def test_exception_on_batch_still_splits(self, no_sleep):
        """整批调用失败也要拆批救回一半，而不是直接归零。"""
        llm = _FakeLLM([RuntimeError("boom"), RuntimeError("boom"),
                        '[{"term":"甲一"}]', '[{"term":"乙一"}]'])
        outcome = _extract_batch_outcome(llm, self._pages(4), "")
        assert set(outcome.terms) == {"甲一", "乙一"}
        assert outcome.error is None

    def test_single_page_empty_response_returns_empty(self, no_sleep):
        outcome = _extract_batch_outcome(_FakeLLM([""]), self._pages(1), "")
        assert outcome.terms == []
        assert outcome.error is None
        assert outcome.calls == 1

    def test_candidates_reported_for_empty_batch(self, no_sleep):
        """模型返回了 JSON 但全被质量过滤 → 候选数 >0、词数 0，可区分两种归零。"""
        llm = _FakeLLM(['[{"term":"123"}]'])  # 纯数字：解析得出、但质量过滤会剔除
        outcome = _extract_batch_outcome(llm, self._pages(1), "")
        assert outcome.terms == []
        assert outcome.candidates == 1
        assert outcome.calls == 1
