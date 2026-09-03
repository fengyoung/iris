"""feed 包单元测试 — 消息噪音过滤（内部函数 + MessageFilter）。"""

from datetime import datetime, timezone

from iris.feed._message_filter import (
    _is_system_message,
    _is_too_short,
    _is_noise_pattern,
    _is_pure_forward,
    MessageFilter,
)
from iris.feed._types import RawMessage


def _make_msg(content, msg_type="text", has_doc_link=False, sender_name="张三"):
    """创建测试用 RawMessage 的辅助函数。"""
    return RawMessage(
        msg_id="mid_1", chat_id="c1", chat_name="群1",
        chat_type="group", sender_id="s1", sender_name=sender_name,
        content=content, raw_content={"text": content},
        msg_type=msg_type, send_time=datetime.now(timezone.utc),
        has_doc_link=has_doc_link,
    )


# ═══════════════════════════════════════════════════════════════
# _is_system_message 测试
# ═══════════════════════════════════════════════════════════════

class TestIsSystemMessage:
    """系统消息判定测试。"""

    def test_join_group(self):
        """「加入了群聊」应判定为系统消息。"""
        assert _is_system_message(_make_msg("张三加入了群聊")) is True

    def test_exit_group(self):
        """「退出了群聊」应判定为系统消息。"""
        assert _is_system_message(_make_msg("李四退出了群聊")) is True

    def test_modify_group_name(self):
        """「修改了群名」应判定为系统消息。"""
        assert _is_system_message(_make_msg("王五修改了群名为新名字")) is True

    def test_group_announcement(self):
        """「群公告」相关消息应判定为系统消息。"""
        assert _is_system_message(_make_msg("管理员发布了群公告")) is True

    def test_group_dissolved(self):
        """「已解散该群」应判定为系统消息。"""
        assert _is_system_message(_make_msg("管理员已解散该群")) is True

    def test_owner_transfer(self):
        """「已将群主转让给」应判定为系统消息。"""
        assert _is_system_message(_make_msg("张三已将群主转让给李四")) is True

    def test_removed_from_group(self):
        """「已被移出群聊」应判定为系统消息。"""
        assert _is_system_message(_make_msg("王五已被移出群聊")) is True

    def test_normal_message_not_system(self):
        """正常消息不应被判定为系统消息。"""
        assert _is_system_message(_make_msg("今天我们讨论一下技术方案")) is False

    def test_empty_content_not_system(self):
        """空内容不应判定为系统消息。"""
        assert _is_system_message(_make_msg("")) is False


# ═══════════════════════════════════════════════════════════════
# _is_too_short 测试
# ═══════════════════════════════════════════════════════════════

class TestIsTooShort:
    """消息过短判定测试。"""

    def test_short_message(self):
        """长度小于 min_length（默认10）的消息应判定为短。"""
        assert _is_too_short(_make_msg("短")) is True

    def test_long_enough(self):
        """长度达到 min_length 的消息不应判定为短。"""
        assert _is_too_short(_make_msg("这是一条足够长的消息内容测试")) is False

    def test_doc_link_not_short(self):
        """含文档链接的短消息不应被判定为短。"""
        msg = _make_msg("文档", has_doc_link=True)
        assert _is_too_short(msg) is False

    def test_image_msg_short(self):
        """图片类型消息应判定为短（无实质文字）。"""
        msg = _make_msg("图", msg_type="image")
        assert _is_too_short(msg) is True

    def test_sticker_msg_short(self):
        """贴图类型消息应判定为短。"""
        msg = _make_msg("", msg_type="sticker")
        assert _is_too_short(msg) is True

    def test_pure_numbers_short(self):
        """纯数字/日期内容应判定为短。"""
        assert _is_too_short(_make_msg("2026-7-28")) is True  # 9 字符，触发纯数字/日期判定
        assert _is_too_short(_make_msg("12345")) is True
        assert _is_too_short(_make_msg("10:30")) is True

    def test_custom_min_length(self):
        """自定义 min_length 参数生效。"""
        msg = _make_msg("正好十字长度")
        assert _is_too_short(msg, min_length=20) is True
        assert _is_too_short(msg, min_length=5) is False

    def test_whitespace_trimmed(self):
        """前后空白应被去除后再计算长度。"""
        assert _is_too_short(_make_msg("  短  ")) is True

    def test_empty_content_short(self):
        """空内容应判定为短。"""
        assert _is_too_short(_make_msg("")) is True


# ═══════════════════════════════════════════════════════════════
# _is_noise_pattern 测试
# ═══════════════════════════════════════════════════════════════

class TestIsNoisePattern:
    """噪音关键词匹配测试。"""

    def test_red_packet(self):
        """含「红包」关键词应匹配。"""
        assert _is_noise_pattern(_make_msg("恭喜发财，红包来了！")) is True
        assert _is_noise_pattern(_make_msg("已领取红包")) is True
        assert _is_noise_pattern(_make_msg("拼手气红包")) is True

    def test_checkin(self):
        """含「接龙」「打卡」「签到」应匹配。"""
        assert _is_noise_pattern(_make_msg("今日接龙")) is True
        assert _is_noise_pattern(_make_msg("打卡签到")) is True

    def test_plus_one(self):
        """纯「+1」应匹配。"""
        assert _is_noise_pattern(_make_msg("+1")) is True

    def test_shoudao(self):
        """纯「收到」应匹配。"""
        assert _is_noise_pattern(_make_msg("收到")) is True

    def test_arrow_emojis(self):
        """纯箭头表情应匹配。"""
        assert _is_noise_pattern(_make_msg("👆")) is True
        assert _is_noise_pattern(_make_msg("👆👆👆")) is True

    def test_normal_message_not_noise(self):
        """正常消息不应匹配噪音模式。"""
        assert _is_noise_pattern(_make_msg("这个方案需要修改一下")) is False

    def test_empty_not_noise(self):
        """空内容不应匹配。"""
        assert _is_noise_pattern(_make_msg("")) is False


# ═══════════════════════════════════════════════════════════════
# _is_pure_forward 测试
# ═══════════════════════════════════════════════════════════════

class TestIsPureForward:
    """纯转发判定测试。"""

    def test_link_only(self):
        """仅含链接的消息应判定为纯转发。"""
        assert _is_pure_forward(_make_msg("https://example.com/doc/123")) is True

    def test_link_with_text(self):
        """含文字描述的链接不应判定为纯转发。"""
        assert _is_pure_forward(_make_msg("这是方案文档 https://example.com/doc")) is False

    def test_image_no_comment(self):
        """图片类型且无评论应判定为纯转发。"""
        msg = _make_msg("图", msg_type="image")
        assert _is_pure_forward(msg) is True

    def test_image_with_comment(self):
        """图片类型但有较长文字评论不应判定为纯转发。"""
        msg = _make_msg("这张图展示了最新的设计方案效果", msg_type="image")
        assert _is_pure_forward(msg) is False

    def test_empty_content(self):
        """空内容不应判定为纯转发。"""
        assert _is_pure_forward(_make_msg("")) is False


# ═══════════════════════════════════════════════════════════════
# MessageFilter 集成测试
# ═══════════════════════════════════════════════════════════════

class TestMessageFilter:
    """MessageFilter 整体过滤器测试。"""

    def test_empty_input(self):
        """空输入返回空字典。"""
        f = MessageFilter()
        assert f.filter({}) == {}

    def test_filter_mixed_chats(self):
        """混合有效和无效消息的多个会话。"""
        f = MessageFilter(min_msg_length=10)
        good = _make_msg("这是一条足够长的有效消息内容")
        noise = _make_msg("红包来了")
        short = _make_msg("短")
        system = _make_msg("张三加入了群聊")
        result = f.filter({
            "c1": [good],
            "c2": [noise],
            "c3": [short],
            "c4": [system],
        })
        assert "c1" in result
        assert "c2" not in result
        assert "c3" not in result
        assert "c4" not in result

    def test_filter_all_noise(self):
        """全部为噪音时返回空字典。"""
        f = MessageFilter()
        result = f.filter({"c1": [_make_msg("收到"), _make_msg("+1")]})
        assert result == {}

    def test_is_noise_system(self):
        """is_noise 对系统消息返回 True。"""
        f = MessageFilter()
        assert f.is_noise(_make_msg("张三退出了群聊")) is True

    def test_is_noise_pattern(self):
        """is_noise 对噪音模式返回 True。"""
        f = MessageFilter()
        assert f.is_noise(_make_msg("接龙开始")) is True

    def test_is_noise_short(self):
        """is_noise 对过短消息返回 True。"""
        f = MessageFilter(min_msg_length=20)
        assert f.is_noise(_make_msg("短消息")) is True

    def test_is_noise_forward(self):
        """is_noise 对纯转发返回 True。"""
        f = MessageFilter()
        assert f.is_noise(_make_msg("https://example.com")) is True

    def test_is_noise_valid(self):
        """is_noise 对有效消息返回 False。"""
        f = MessageFilter()
        msg = _make_msg("这是一个有实质内容的讨论消息，关于技术方案的设计思路")
        assert f.is_noise(msg) is False

    def test_filter_keeps_good_only(self):
        """单会话中仅保留有效消息。"""
        f = MessageFilter(min_msg_length=8)
        good = _make_msg("有效讨论消息内容")
        bad = _make_msg("短")
        result = f.filter({"c1": [good, bad]})
        assert len(result["c1"]) == 1
        assert result["c1"][0].content == "有效讨论消息内容"
