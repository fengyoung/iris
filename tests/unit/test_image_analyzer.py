"""MessageImageAnalyzer 单元测试。"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from iris.feed._types import RawMessage
from iris.feishu.image_analyzer import MessageImageAnalyzer

# 1x1 透明 PNG 的魔数（仅用于让 _to_data_url 读到合法字节）
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff0000000049454e44ae426082"
)


def _make_raw(image_key: str, msg_id: str = "om_test", extra_content: str = ""):
    """构造 image 类型的 RawMessage（raw_content 内含 body.content JSON）。"""
    return RawMessage(
        msg_id=msg_id,
        chat_id="c1",
        chat_name="群A",
        chat_type="group",
        sender_id="s1",
        sender_name="张三",
        content=f"[Image: {image_key}] {extra_content}",
        raw_content={"body": {"content": f'{{"image_key": "{image_key}"}}'}},
        msg_type="image",
        send_time=datetime.now(timezone.utc),
    )


def _write_png(cache_dir: Path, image_key: str) -> Path:
    img_dir = cache_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    target = img_dir / f"{image_key}.png"
    target.write_bytes(_PNG_BYTES)
    return target


@pytest.fixture
def analyzer(tmp_path):
    return MessageImageAnalyzer(
        Mock(), Mock(), cache_dir=tmp_path, enabled=True, max_per_run=10
    )


# ── image_key 提取 ─────────────────────────────────────────────


def test_extract_image_key_from_json_body():
    key = MessageImageAnalyzer._extract_image_key(
        {"body": {"content": '{"image_key": "img_v3_abc_123"}'}},
        fallback="",
    )
    assert key == "img_v3_abc_123"


def test_extract_image_key_from_text_fallback():
    key = MessageImageAnalyzer._extract_image_key(
        {"body": {"content": ""}}, fallback="[Image: img_v3_def_09]"
    )
    assert key == "img_v3_def_09"


def test_extract_image_key_negative():
    assert MessageImageAnalyzer._extract_image_key({}, fallback="普通文本") == ""
    assert MessageImageAnalyzer._extract_image_key(
        {"body": {"content": '{"other": "x"}'}}, fallback=""
    ) == ""


def test_describe_dict_message_extracts_key():
    d = {
        "message_id": "om_1",
        "msg_type": "image",
        "body": {"content": '{"image_key": "img_v3_dictkey"}'},
    }
    result = MessageImageAnalyzer._extract_image_key(d, fallback=d.get("image_key", ""))
    assert result == "img_v3_dictkey"


# ── 成功路径 + 缓存 ────────────────────────────────────────────


def test_success_and_cache_hit(tmp_path):
    llm = Mock()
    llm.generate_multimodal.return_value = "一张数据看板，总直检率45.7%"
    client = Mock()
    analyzer = MessageImageAnalyzer(
        client, llm, cache_dir=tmp_path, enabled=True, max_per_run=10
    )
    key = "img_v3_cachetest"
    _write_png(tmp_path, key)

    msg = _make_raw(key)
    desc1 = analyzer.describe_raw_message(msg)

    assert desc1 == "一张数据看板，总直检率45.7%"
    assert analyzer._used == 1
    # 下载方法未被真正调用（文件已存在，复用缓存文件）
    client.download_message_image.assert_not_called()

    # 第二次同 key → 缓存命中，不再调 LLM
    desc2 = analyzer.describe_raw_message(_make_raw(key))
    assert desc2 == "一张数据看板，总直检率45.7%"
    assert analyzer._used == 1
    llm.generate_multimodal.assert_called_once()


# ── 预算 / 开关 ────────────────────────────────────────────────


def test_budget_exhausted(tmp_path):
    llm = Mock()
    llm.generate_multimodal.return_value = "描述"
    analyzer = MessageImageAnalyzer(
        Mock(), llm, cache_dir=tmp_path, enabled=True, max_per_run=1
    )
    key_a, key_b = "img_v3_a", "img_v3_b"
    _write_png(tmp_path, key_a)
    _write_png(tmp_path, key_b)

    assert analyzer.describe_raw_message(_make_raw(key_a)) == "描述"
    assert analyzer._used == 1
    # 预算耗尽 → 第二张不分析，返回 None
    assert analyzer.describe_raw_message(_make_raw(key_b)) is None
    assert llm.generate_multimodal.call_count == 1


def test_disabled_returns_none(tmp_path):
    llm = Mock()
    analyzer = MessageImageAnalyzer(
        Mock(), llm, cache_dir=tmp_path, enabled=False, max_per_run=10
    )
    key = "img_v3_disabled"
    _write_png(tmp_path, key)
    assert analyzer.describe_raw_message(_make_raw(key)) is None
    llm.generate_multimodal.assert_not_called()


# ── 下载失败 ───────────────────────────────────────────────────


def test_download_failure_returns_none(tmp_path):
    llm = Mock()
    client = Mock()
    client.download_message_image.side_effect = Exception("下载失败")
    analyzer = MessageImageAnalyzer(
        client, llm, cache_dir=tmp_path, enabled=True, max_per_run=10
    )
    key = "img_v3_nofile"
    # 不写缓存文件，强制走下载失败路径
    assert analyzer.describe_raw_message(_make_raw(key)) is None
    llm.generate_multimodal.assert_not_called()


# ── describe_dict_message 成功路径 ─────────────────────────────


def test_describe_dict_message(tmp_path):
    llm = Mock()
    llm.generate_multimodal.return_value = "命名方案表格"
    analyzer = MessageImageAnalyzer(
        Mock(), llm, cache_dir=tmp_path, enabled=True, max_per_run=10
    )
    key = "img_v3_dictok"
    _write_png(tmp_path, key)
    d = {
        "message_id": "om_9",
        "msg_type": "image",
        "body": {"content": f'{{"image_key": "{key}"}}'},
    }
    assert analyzer.describe_dict_message(d) == "命名方案表格"


# ── content_for_prompt 三级回退 ────────────────────────────────


def test_content_for_prompt_fallback():
    base = _make_raw("img_v3_t")
    base.msg_type = "text"
    assert base.content_for_prompt() == base.content

    img = _make_raw("img_v3_t")
    img.image_description = ""
    assert img.content_for_prompt() == "[图片]"

    img.image_description = "一张数据看板"
    assert img.content_for_prompt() == "（图片：一张数据看板）"
