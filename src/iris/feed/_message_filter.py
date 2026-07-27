"""信息汇聚管道 — 消息噪音过滤。

规则过滤：红包/接龙/太短/纯表情/系统消息/纯转发
"""

from __future__ import annotations

import re
from typing import Dict, List

from iris.feed._types import RawMessage

# ── 噪音判定规则 ──────────────────────────────────────

# 纯系统消息关键字
_SYSTEM_KEYWORDS = [
    "加入了群聊", "退出了群聊", "修改了群名", "群公告",
    "已解散该群", "已将群主转让给", "已被移出群聊",
    "开启了群认证", "关闭了群认证", "邀请你加入",
]

# 红包/打卡相关
_NOISE_PATTERNS = [
    re.compile(r"红包|已领取|拼手气|专属红包"),
    re.compile(r"接龙|打卡|签到"),
    re.compile(r"^\+1$"),
    re.compile(r"^收到$"),
    re.compile(r"^👆+$"),
]


def _is_system_message(msg: RawMessage) -> bool:
    """是否为系统消息。"""
    for kw in _SYSTEM_KEYWORDS:
        if kw in msg.content:
            return True
    return False


def _is_too_short(msg: RawMessage, min_length: int = 10) -> bool:
    """消息是否过短且无实质内容。"""
    text = msg.content.strip()
    if len(text) >= min_length:
        return False
    # 有文档链接的不算短
    if msg.has_doc_link:
        return False
    # 纯表情/贴图
    if msg.msg_type in ("image", "sticker"):
        return True
    # 纯数字/日期
    if re.match(r'^[\d\-/:.,]+$', text):
        return True
    return len(text) < min_length


def _is_noise_pattern(msg: RawMessage) -> bool:
    """是否命中噪音关键词。"""
    for pat in _NOISE_PATTERNS:
        if pat.search(msg.content):
            return True
    return False


def _is_pure_forward(msg: RawMessage) -> bool:
    """是否为无评论的纯转发（仅一个链接无文字）。"""
    content = msg.content.strip()
    if not content:
        return False
    # 只有链接
    link_only = re.match(r'^https?://\S+$', content)
    if link_only:
        return True
    # 含图片但无文字评论
    if msg.msg_type == "image" and len(content) < 10:
        return True
    return False


class MessageFilter:
    """消息噪音过滤器。"""

    def __init__(self, min_msg_length: int = 10):
        self.min_length = min_msg_length

    def filter(
        self,
        messages: Dict[str, List[RawMessage]],
    ) -> Dict[str, List[RawMessage]]:
        """过滤噪音，返回 {chat_id: [有效消息...]}。"""
        result: Dict[str, List[RawMessage]] = {}
        for chat_id, msgs in messages.items():
            kept = [m for m in msgs if not self.is_noise(m)]
            if kept:
                result[chat_id] = kept
        return result

    def is_noise(self, msg: RawMessage) -> bool:
        """判断单条消息是否为噪音。"""
        if _is_system_message(msg):
            return True
        if _is_noise_pattern(msg):
            return True
        if _is_too_short(msg, self.min_length):
            return True
        if _is_pure_forward(msg):
            return True
        return False
