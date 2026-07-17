"""集成测试 — 需要文件系统、mock 外部服务或真实 I/O。"""
import pytest

pytestmark = [pytest.mark.integration]
