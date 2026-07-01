"""核心类型和工具模块测试。"""

from __future__ import annotations

from iris.core.fake_provider import FakeLLMProvider
from iris.core.llm_types import LLMRequest
from iris.core.locks import FileLock
from iris.utils.tokenization import estimate_tokens, truncate_by_tokens


class TestFakeLLMProvider:
    def test_generate(self):
        provider = FakeLLMProvider()
        request = LLMRequest(prompt="test prompt", route_context={"task_type": "qa"})
        response = provider.generate(request)
        assert response.text.strip() != ""
        assert response.selected_role == "base_model"

    def test_generate_multimodal(self):
        provider = FakeLLMProvider()
        result = provider.generate_multimodal([{"type": "text", "text": "hello"}], {})
        assert isinstance(result, str)


class TestFileLock:
    def test_acquire_release(self, tmp_path):
        target = tmp_path / "test.json"
        lock = FileLock(target)
        lock.acquire()
        # 锁文件实际路径是 target + ".lock"
        assert (tmp_path / "test.json.lock").exists()
        lock.release()

    def test_context_manager(self, tmp_path):
        target = tmp_path / "ctx.json"
        with FileLock(target) as lock_obj:
            assert lock_obj is not None
            assert (tmp_path / "ctx.json.lock").exists()


class TestTokenization:
    def test_estimate_tokens(self):
        count = estimate_tokens("Hello World")
        assert count > 0

    def test_truncate(self):
        text = "Hello World " * 100
        truncated = truncate_by_tokens(text, max_tokens=50)
        assert len(truncated) < len(text)
