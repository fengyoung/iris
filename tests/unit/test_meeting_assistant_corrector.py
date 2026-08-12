"""校正引擎单元测试：Aho-Corasick 替换 / 上下文窗口 / LLM 深度校正。

AC 自动机使用真实 pyahocorasick（纯计算、无外部依赖）；
LLM 使用 duck-typed FakeLLM（与 analyzer 测试模式一致）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from iris.assistant._corrector import CorrectorAdapter


# ── FakeLLM ───────────────────────────────────────────────────

class _FakeLLM:
    """Duck-typed LLM 服务（与 analyzer 测试的 _FakeLLM 模式一致）。"""

    def __init__(self, text: str = "", raise_on_generate: bool = False):
        self._text = text
        self.raise_on_generate = raise_on_generate
        self.calls: list[dict] = []

    def generate(self, prompt: str, route_context=None, **kwargs):
        self.calls.append({"prompt": prompt, "route_context": route_context, "kwargs": kwargs})
        if self.raise_on_generate:
            raise RuntimeError("fake LLM failure")
        return SimpleNamespace(text=self._text)


# ── 工具 ──────────────────────────────────────────────────────

def _make_corrector(replace_dict=None, llm_prompt="", llm_timeout_ms=8000):
    return CorrectorAdapter(
        replace_dict=replace_dict or {},
        llm_prompt=llm_prompt,
        llm_timeout_ms=llm_timeout_ms,
    )


# ── TestACReplace ─────────────────────────────────────────────

class TestACReplace:
    """Aho-Corasick 词典替换。"""

    def test_simple_replace(self):
        """单条映射替换。"""
        c = _make_corrector({"图象": "图像"})
        assert c.fast("做图象识别") == "做图像识别"

    def test_multi_replace(self):
        """多条映射按匹配位置替换。"""
        c = _make_corrector({"图象": "图像", "算发": "算法"})
        result = c.fast("图象识别和算发优化")
        assert result == "图像识别和算法优化"

    def test_backward_replace_no_offset_bug(self):
        """从后往前替换避免偏移（构造重叠映射验证）。"""
        c = _make_corrector({"AB": "12", "BC": "34"})
        result = c.fast("ABC")
        # 从后往前：先匹配 "BC"(end=2)→"34"，再匹配 "AB"(end=1)
        # "ABC" → "A" + "BC"→"34" = "A34" → 然后 AB 匹配位置 0-1 = "A3"
        # chars=['A','3','4'] → chars[0:2]=['1','2'] → "124"
        assert result in ("124", "A34")  # 两种合理实现均接受

    def test_identity_map_skipped(self):
        """key==value 的恒等映射不加入自动机。"""
        c = _make_corrector({"图像": "图像", "算法": "算法"})
        # 恒等映射应被跳过，替换结果与原文字相同
        assert c.fast("图像算法") == "图像算法"

    def test_empty_key_or_value_skipped(self):
        """空 key 或空 value 跳过。"""
        c = _make_corrector({"": "x", "y": "", "图象": "图像"})
        # 空 key/value 被跳过，仅有效映射生效
        assert c.fast("图象") == "图像"

    def test_no_match_returns_original(self):
        """无匹配时返回原文。"""
        c = _make_corrector({"图象": "图像"})
        assert c.fast("测试文本") == "测试文本"

    def test_automaton_corrupt_fallback(self):
        """自动机损坏时返回原文（替换自动机为抛异常的 mock）。"""
        c = _make_corrector({"图象": "图像"})
        bad_auto = MagicMock()
        bad_auto.iter.side_effect = Exception("corrupt")
        old = c._automaton
        c._automaton = bad_auto
        try:
            assert c.fast("图象识别") == "图象识别"
        finally:
            c._automaton = old


# ── TestContext ───────────────────────────────────────────────

class TestContext:
    """近期上下文窗口。"""

    def test_context_appended_and_retrieved(self):
        """push 后 build_context 返回对应内容。"""
        c = _make_corrector()
        c.push_context("第一句识别结果")
        c.push_context("第二句识别结果")
        ctx = c._build_context()
        assert "第一句识别结果" in ctx
        assert "第二句识别结果" in ctx

    def test_context_expires(self):
        """超过 10 分钟的上下文被过滤。"""
        c = _make_corrector()
        c.push_context("旧文本")
        # 手动将时间戳回退到过期
        c._recent[0] = (c._recent[0][0], c._recent[0][1] - 601)
        ctx = c._build_context()
        assert ctx == ""

    def test_deque_maxlen_enforced(self):
        """超过 5 条旧条目滚出。"""
        c = _make_corrector()
        for i in range(7):
            c.push_context(f"第{i}句话")
        # deque maxlen=5，前两条已滚出
        ctx = c._build_context()
        assert "第0句话" not in ctx
        assert "第1句话" not in ctx
        assert "第6句话" in ctx


# ── TestLLMDeepCorrection ─────────────────────────────────────

class TestLLMDeepCorrection:
    """LLM 深度校正。"""

    def test_no_llm_falls_back_to_input(self):
        """未注入 LLM 时 deep 返回原文。"""
        c = _make_corrector({"图象": "图像"}, llm_prompt="修正：{{text}}")
        assert c.deep("图象识别") == "图象识别"

    def test_no_prompt_falls_back_to_input(self):
        """无 prompt 时 deep 返回原文。"""
        c = _make_corrector({"图象": "图像"})
        c.set_llm_service(_FakeLLM(text="图像识别"))
        assert c.deep("图象识别") == "图象识别"

    def test_prompt_template_substitution(self):
        """{{context}} 和 {{text}} 被正确替换。"""
        # LLM 输出须与输入足够相似以通过 _is_similar 校验
        llm = _FakeLLM(text="原始文本已修正")
        c = _make_corrector(
            {"图象": "图像"},
            llm_prompt="上文：{{context}}\n修正：{{text}}",
        )
        c.set_llm_service(llm)
        c.push_context("上一句的内容")
        result = c.deep("原始文本")
        # 验证 prompt 中模板被替换
        prompt = llm.calls[0]["prompt"]
        assert "上一句的内容" in prompt
        assert "原始文本" in prompt
        assert "{{context}}" not in prompt
        assert "{{text}}" not in prompt
        assert result == "原始文本已修正"

    def test_llm_exception_falls_back(self):
        """LLM 调用异常时返回原文。"""
        llm = _FakeLLM(raise_on_generate=True)
        c = _make_corrector({}, llm_prompt="修正：{{text}}")
        c.set_llm_service(llm)
        assert c.deep("原文") == "原文"

    def test_dissimilar_output_rejected(self):
        """LLM 输出与原文 ratio < 0.5 时丢弃（防幻觉）。"""
        llm = _FakeLLM(text="完全不相关的长文本" * 10)
        c = _make_corrector({}, llm_prompt="修正：{{text}}")
        c.set_llm_service(llm)
        # 短输入 "原文" 与长输出 ratio 远小于 0.5
        assert c.deep("原文") == "原文"

    def test_similar_output_accepted(self):
        """LLM 输出与原文相似时接受。"""
        llm = _FakeLLM(text="图像识别与算法优化")  # 与输入接近
        c = _make_corrector({}, llm_prompt="修正：{{text}}")
        c.set_llm_service(llm)
        # "图象识别和算发优化" vs "图像识别与算法优化" — ratio ~0.8
        result = c.deep("图象识别和算发优化")
        assert result == "图像识别与算法优化"

    def test_deadline_passed_to_llm(self):
        """_deadline 参数正确传递给 LLM。"""
        import time
        llm = _FakeLLM(text="修正后文本")
        c = _make_corrector({}, llm_prompt="修正：{{text}}", llm_timeout_ms=5000)
        c.set_llm_service(llm)
        before = time.monotonic()
        c.deep("测试")
        deadline = llm.calls[0]["kwargs"].get("_deadline")
        assert deadline is not None
        # deadline 应在调用时刻 + timeout 范围内
        expected = before + 5.0  # 5000ms
        assert deadline == pytest.approx(expected, abs=0.2)


# ── TestIsSimilar ─────────────────────────────────────────────

class TestIsSimilar:
    """自适应相似度阈值（短文本 0.5 / 长文本 0.35）。"""

    def test_short_text_threshold(self):
        """短文本（< 20 字符）使用 0.5 阈值。"""
        # "好的谢谢" vs "好的谢谢。" ratio ~0.89 → 接受
        assert CorrectorAdapter._is_similar("好的谢谢", "好的谢谢。")
        # "好的" vs "不对不对不对不对不对" ratio ~0.15 → 拒绝
        assert not CorrectorAdapter._is_similar("好的", "不对不对不对不对不对")

    def test_long_text_threshold(self):
        """长文本（≥ 20 字符）使用 0.35 阈值，容忍更多变化。"""
        long_a = "今天我们要讨论一下关于图像识别算法优化的方案"
        long_b = "今天我们讨论的是图像识别和算法优化的相关方案"
        # ratio ~0.7 > 0.35 → 接受
        assert CorrectorAdapter._is_similar(long_a, long_b)

    def test_explicit_threshold_override(self):
        """显式 threshold 参数覆盖自适应逻辑。"""
        # 短文本但强行要求高阈值
        assert CorrectorAdapter._is_similar("abc", "abd", threshold=0.9) is False
        assert CorrectorAdapter._is_similar("abc", "abd", threshold=0.5) is True
